"""`make benchmark`: line protocol ingest and InfluxQL query latency against the InfluxDB 1.8
cluster fork. Standard library only (uv run --frozen in the uv image).

1. recreates database `bench` WITH REPLICATION 2 (every shard on both data nodes);
2. ingest: ROWS rows of `cpu,host=host-N,region=rM usage_user=,usage_system=,usage_idle=,load=`
   for SERIES hosts, one point per host every 10 s ending now, pre-built in requests of BATCH
   lines and posted to /write?consistency=CONSISTENCY by WRITERS threads, alternating between the
   data nodes in WRITE_HOSTS (keep-alive); reports rows/s, MB/s and per-request latency;
3. queries: each InfluxQL query runs ITER times through /query on the first data node, p50/p99.
Writes results/$NAME.json and prints a table."""

import http.client
import json
import os
import platform
import random
import statistics
import threading
import time
import urllib.parse
from datetime import datetime, timezone

WRITE_HOSTS = [(h.split(":")[0], int(h.split(":")[1])) for h in
               os.environ.get("WRITE_HOSTS", "data-1:8086,data-2:8086").split(",")]
DB = "bench"
NAME = os.environ["NAME"]
ROWS = int(os.environ.get("ROWS", "2000000"))
SERIES = int(os.environ.get("SERIES", "10000"))
BATCH = int(os.environ.get("BATCH", "10000"))
WRITERS = int(os.environ.get("WRITERS", "16"))
CONSISTENCY = os.environ.get("CONSISTENCY", "one")
ITER = int(os.environ.get("ITER", "30"))


def conn(target):
    return http.client.HTTPConnection(*target, timeout=300)


def request(c, method, path, body=None):
    c.request(method, path, body=body)
    resp = c.getresponse()
    data = resp.read()
    if resp.status >= 300:
        raise RuntimeError(f"{method} {path[:80]} -> {resp.status}: {data[:300]!r}")
    return data


def influxql(c, q, db=DB):
    data = json.loads(request(c, "POST", "/query?" + urllib.parse.urlencode({"db": db, "q": q})))
    res = data["results"][0]
    if "error" in res:
        raise RuntimeError(res["error"])
    return sum(len(s["values"]) for s in res.get("series", []))


def build_batches():
    rnd = random.Random(42)
    steps = -(-ROWS // SERIES)
    end = time.time_ns() // 1_000_000_000 * 1_000_000_000
    hosts = [f"cpu,host=host-{h},region=r{h % 16} ".encode() for h in range(SERIES)]
    bodies, cur, n = [], [], 0
    for s in range(steps):
        ts = b" %d\n" % (end - (steps - 1 - s) * 10_000_000_000)
        for h in range(SERIES):
            if n == ROWS:
                break
            u = rnd.random() * 100
            cur.append(hosts[h] + b"usage_user=%.2f,usage_system=%.2f,usage_idle=%.2f,load=%di" % (
                u, u / 4, 100 - u, rnd.randrange(64)) + ts)
            n += 1
            if len(cur) == BATCH:
                bodies.append(b"".join(cur))
                cur = []
    if cur:
        bodies.append(b"".join(cur))
    return bodies, sum(map(len, bodies))


def ingest(bodies):
    lat, lock, it, errors = [], threading.Lock(), iter(bodies), []
    path = f"/write?db={DB}&precision=ns&consistency={CONSISTENCY}"

    def worker(target):
        c = conn(target)
        while True:
            with lock:
                body = next(it, None)
            if body is None:
                break
            t0 = time.perf_counter()
            try:
                request(c, "POST", path, body)
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
                c.close()
                c = conn(target)
                continue
            with lock:
                lat.append(time.perf_counter() - t0)
        c.close()

    threads = [threading.Thread(target=worker, args=(WRITE_HOSTS[i % len(WRITE_HOSTS)],)) for i in range(WRITERS)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return time.perf_counter() - t0, lat, errors


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def run_queries():
    h = f"host-{SERIES // 2}"
    queries = [
        ("count all rows", "SELECT count(usage_user) FROM cpu"),
        ("mean by region, last 5 min", "SELECT mean(usage_user) FROM cpu WHERE time > now() - 5m GROUP BY region"),
        ("1 host, all points", f"SELECT usage_user FROM cpu WHERE host = '{h}'"),
        ("1 host latest: last()", f"SELECT last(usage_user) FROM cpu WHERE host = '{h}'"),
        ("all hosts latest: last() GROUP BY host", "SELECT last(usage_user) FROM cpu GROUP BY host"),
        ("distinct hosts: SHOW TAG VALUES", "SHOW TAG VALUES FROM cpu WITH KEY = host"),
    ]
    c, out = conn(WRITE_HOSTS[0]), []
    for label, q in queries:
        rows = influxql(c, q)
        ts = []
        for _ in range(ITER):
            t0 = time.perf_counter()
            influxql(c, q)
            ts.append((time.perf_counter() - t0) * 1000)
        out.append({"query": label, "influxql": q, "rows": rows, "iterations": ITER,
                    "p50_ms": round(pct(ts, 50), 2), "p99_ms": round(pct(ts, 99), 2),
                    "mean_ms": round(statistics.mean(ts), 2)})
    c.close()
    return out


def machine():
    with open("/proc/meminfo") as f:
        mem_kb = next(int(line.split()[1]) for line in f if line.startswith("MemTotal:"))
    return {"docker_vm_cpus": os.cpu_count(), "docker_vm_memory_gib": round(mem_kb / 2**20, 1),
            "arch": platform.machine(), "host": os.environ.get("HOST_INFO", ""),
            "docker": os.environ.get("DOCKER_INFO", "")}


def main():
    c = conn(WRITE_HOSTS[0])
    c.request("GET", "/ping")
    r = c.getresponse()
    r.read()
    version = r.getheader("X-Influxdb-Version") or "?"
    influxql(c, f"DROP DATABASE {DB}", db="")
    influxql(c, f"CREATE DATABASE {DB} WITH DURATION 7d REPLICATION 2 SHARD DURATION 1d NAME week", db="")
    c.close()

    t0 = time.perf_counter()
    bodies, nbytes = build_batches()
    print(f"==> built {ROWS:,} lines for {SERIES:,} series in {len(bodies)} requests ({nbytes / 1e6:.0f} MB) "
          f"in {time.perf_counter() - t0:.1f} s; {WRITERS} writers over {len(WRITE_HOSTS)} data nodes, "
          f"consistency={CONSISTENCY}")
    secs, lat, errors = ingest(bodies)
    if errors:
        raise SystemExit(f"{len(errors)} write errors, first: {errors[0]}")
    ing = {"rows": ROWS, "series": SERIES, "requests": len(bodies), "batch_lines": BATCH,
           "writers": WRITERS, "consistency": CONSISTENCY, "bytes": nbytes, "seconds": round(secs, 2),
           "rows_per_s": round(ROWS / secs), "mb_per_s": round(nbytes / 1e6 / secs, 1),
           "request_p50_ms": round(pct(lat, 50) * 1000, 1), "request_p99_ms": round(pct(lat, 99) * 1000, 1)}
    # with consistency=one an ack means one replica has the batch; the other gets it via hinted
    # handoff. Poll count() through each data node (each reads its own copy) until both see ROWS.
    def counts_now():
        out = {}
        for target in WRITE_HOSTS:
            c = conn(target)
            res = json.loads(request(c, "POST", "/query?" + urllib.parse.urlencode(
                {"db": DB, "q": "SELECT count(usage_user) FROM cpu"})))
            out[target[0]] = res["results"][0]["series"][0]["values"][0][1]
            c.close()
        return out

    t0 = time.perf_counter()
    first = counts = counts_now()
    while not all(v >= ROWS for v in counts.values()) and time.perf_counter() - t0 < 120:
        time.sleep(0.2)
        counts = counts_now()
    done = all(v >= ROWS for v in counts.values())
    ing["count_per_node_at_last_ack"] = first
    ing["count_per_node"] = counts
    ing["all_replicas_complete_after_s"] = round(time.perf_counter() - t0, 2) if done else None
    print(f"==> ingest: {ing['rows_per_s']:,} rows/s; rows per data node right after the last ack {first}, "
          f"both complete {ing['all_replicas_complete_after_s']} s later; running {ITER} iterations of each query")
    queries = run_queries()

    result = {"system": "influxdb-cluster (chengshiwen fork)", "version": version,
              "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "machine": machine(), "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),
              "ingest": ing, "queries": queries}
    os.makedirs("/results", exist_ok=True)
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    m = result["machine"]
    print(f"\nInfluxDB cluster {version} (3 meta + 2 data, RF 2) | {ROWS:,} rows, {SERIES:,} series, "
          f"{len(bodies)} requests of {BATCH:,} lines, {WRITERS} writers, consistency={CONSISTENCY}")
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})")
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {v['cpus']} CPUs / {v['memory_mib']} MiB" for n, v in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs")
    print(f"\ningest: {ing['rows_per_s']:,} rows/s ({ing['mb_per_s']} MB/s of line protocol) in {ing['seconds']} s; "
          f"request p50 {ing['request_p50_ms']} ms, p99 {ing['request_p99_ms']} ms\n"
          f"rows per data node right after the last ack: {first}; both replicas complete "
          f"{ing['all_replicas_complete_after_s']} s later\n")
    hdr = f"{'query':<40} {'rows':>7} {'p50 ms':>8} {'p99 ms':>8}"
    print(hdr)
    print("-" * len(hdr))
    for q in queries:
        print(f"{q['query']:<40} {q['rows']:>7,} {q['p50_ms']:>8.2f} {q['p99_ms']:>8.2f}")
    print(f"\nresults/{NAME}.json")


if __name__ == "__main__":
    main()
