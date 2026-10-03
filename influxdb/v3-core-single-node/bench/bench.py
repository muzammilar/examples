"""`make benchmark`: line protocol ingest and query latency against InfluxDB 3 Core over HTTP.
Standard library only (uv run --frozen in the uv image).

1. recreates database `bench`, writes one seed row, then creates a last value cache (key: host)
   and a distinct value cache (region, host) on `cpu` so both fill during the load;
2. ingest: ROWS rows of `cpu,host=host-N,region=rM usage_user=,usage_system=,usage_idle=,load=`
   for SERIES hosts, one point per host every 10 s ending now; batches of BATCH lines (one or more
   timestamps across all hosts) are pre-built, then posted to /api/v3/write_lp by WRITERS threads
   with keep-alive connections; reports rows/s, MB/s and per-request latency. A write returns
   after the next WAL flush (1 s) unless NO_SYNC=1 (`no_sync=true`: ack once buffered);
3. queries: each query in QUERIES runs ITER times through /api/v3/query_sql (JSON), p50/p99 ms.
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

HOST, PORT = os.environ.get("INFLUX_HOST", "influxdb3"), int(os.environ.get("INFLUX_PORT", "8181"))
DB = "bench"
NAME = os.environ["NAME"]
ROWS = int(os.environ.get("ROWS", "2000000"))
SERIES = int(os.environ.get("SERIES", "10000"))
BATCH = int(os.environ.get("BATCH", "10000"))
WRITERS = int(os.environ.get("WRITERS", "4"))
ITER = int(os.environ.get("ITER", "30"))
NO_SYNC = os.environ.get("NO_SYNC", "") in ("1", "true")  # ack before the WAL flush
with open("/token/admin.json") as f:
    TOKEN = json.load(f)["token"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def request(conn, method, path, body=None, headers=None):
    conn.request(method, path, body=body, headers={**HEADERS, **(headers or {})})
    resp = conn.getresponse()
    data = resp.read()
    if resp.status >= 300:
        raise RuntimeError(f"{method} {path} -> {resp.status}: {data[:300]!r}")
    return data


def conn():
    return http.client.HTTPConnection(HOST, PORT, timeout=300)


def setup():
    c = conn()
    try:
        request(c, "DELETE", f"/api/v3/configure/database?db={DB}&hard_delete_at=now")
    except RuntimeError:
        pass  # not there yet
    request(c, "POST", "/api/v3/configure/database", json.dumps({"db": DB}), {"Content-Type": "application/json"})
    # the table must exist before caches can be created on it
    request(c, "POST", f"/api/v3/write_lp?db={DB}&precision=second",
            b"cpu,host=host-0,region=r0 usage_user=0,usage_system=0,usage_idle=100,load=0i 1")
    js = {"Content-Type": "application/json"}
    request(c, "POST", "/api/v3/configure/last_cache", json.dumps({
        "db": DB, "table": "cpu", "name": "cpu_last", "key_columns": ["host"],
        "value_columns": ["usage_user", "usage_system", "usage_idle", "load"], "count": 1, "ttl": 3600}), js)
    request(c, "POST", "/api/v3/configure/distinct_cache", json.dumps({
        "db": DB, "table": "cpu", "name": "cpu_hosts", "columns": ["region", "host"]}), js)
    c.close()


def build_batches():
    """Lines in time order: step t writes one point for every host. Returns (bodies, bytes)."""
    rnd = random.Random(42)
    steps = -(-ROWS // SERIES)
    end = time.time_ns() // 1_000_000_000 * 1_000_000_000
    hosts = [f"cpu,host=host-{h},region=r{h % 16} ".encode() for h in range(SERIES)]
    bodies, cur, n, total = [], [], 0, 0
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
    lat, lock, it = [], threading.Lock(), iter(enumerate(bodies))
    errors = []

    def worker():
        c = conn()
        while True:
            with lock:
                nxt = next(it, None)
            if nxt is None:
                break
            t0 = time.perf_counter()
            try:
                request(c, "POST", f"/api/v3/write_lp?db={DB}&precision=nanosecond{'&no_sync=true' if NO_SYNC else ''}", nxt[1])
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
                c.close()
                c = conn()
                continue
            with lock:
                lat.append(time.perf_counter() - t0)
        c.close()

    threads = [threading.Thread(target=worker) for _ in range(WRITERS)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return time.perf_counter() - t0, lat, errors


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def sql(c, q):
    path = "/api/v3/query_sql?" + urllib.parse.urlencode({"db": DB, "q": q, "format": "json"})
    return json.loads(request(c, "GET", path))


def run_queries():
    h = f"host-{SERIES // 2}"
    queries = [
        ("count(*) all rows", "SELECT count(*) AS n FROM cpu"),
        ("avg by region, last 5 min", "SELECT region, avg(usage_user) AS u FROM cpu "
         "WHERE time > now() - INTERVAL '5 minutes' GROUP BY region"),
        ("1 host, all points", f"SELECT time, usage_user FROM cpu WHERE host = '{h}' ORDER BY time"),
        ("1 host latest: SQL", f"SELECT * FROM cpu WHERE host = '{h}' ORDER BY time DESC LIMIT 1"),
        ("1 host latest: last_cache", f"SELECT * FROM last_cache('cpu', 'cpu_last') WHERE host = '{h}'"),
        ("all hosts latest: SQL", "SELECT host, max(time) AS t, last_value(usage_user ORDER BY time) AS u "
         "FROM cpu GROUP BY host"),
        ("all hosts latest: last_cache", "SELECT host, time, usage_user FROM last_cache('cpu', 'cpu_last')"),
        ("distinct hosts: SQL", "SELECT DISTINCT region, host FROM cpu"),
        ("distinct hosts: distinct_cache", "SELECT region, host FROM distinct_cache('cpu', 'cpu_hosts')"),
    ]
    c, out = conn(), []
    for label, q in queries:
        rows = len(sql(c, q))  # warm-up, and the row count
        ts = []
        for _ in range(ITER):
            t0 = time.perf_counter()
            sql(c, q)
            ts.append((time.perf_counter() - t0) * 1000)
        out.append({"query": label, "sql": q, "rows": rows, "iterations": ITER,
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
    c = conn()
    version = json.loads(request(c, "GET", "/ping")).get("version", "?")
    c.close()

    setup()
    t0 = time.perf_counter()
    bodies, nbytes = build_batches()
    print(f"==> built {ROWS:,} lines for {SERIES:,} series in {len(bodies)} requests "
          f"({nbytes / 1e6:.0f} MB) in {time.perf_counter() - t0:.1f} s; writing with {WRITERS} writers")
    secs, lat, errors = ingest(bodies)
    if errors:
        raise SystemExit(f"{len(errors)} write errors, first: {errors[0]}")
    ing = {"rows": ROWS, "series": SERIES, "requests": len(bodies), "batch_lines": BATCH,
           "writers": WRITERS, "no_sync": NO_SYNC, "bytes": nbytes, "seconds": round(secs, 2),
           "rows_per_s": round(ROWS / secs), "mb_per_s": round(nbytes / 1e6 / secs, 1),
           "request_p50_ms": round(pct(lat, 50) * 1000, 1), "request_p99_ms": round(pct(lat, 99) * 1000, 1)}
    # with no_sync the ack comes before the WAL flush that makes rows queryable: wait for all of them
    c, t0, seen = conn(), time.perf_counter(), 0
    while time.perf_counter() - t0 < 120:
        seen = sql(c, "SELECT count(*) AS n FROM cpu")[0]["n"] - 1  # minus the seed row
        if seen >= ROWS:
            break
        time.sleep(0.05)
    c.close()
    ing["all_visible_after_s"] = round(time.perf_counter() - t0, 2) if seen >= ROWS else None
    print(f"==> ingest: {ing['rows_per_s']:,} rows/s; all rows queryable {ing['all_visible_after_s']} s "
          f"after the last ack; running {ITER} iterations of each query")
    queries = run_queries()
    c = conn()
    pf = sql(c, "SELECT count(*) AS files, sum(size_bytes) AS bytes, sum(row_count) AS rows "
                "FROM system.parquet_files WHERE table_name = 'cpu'")
    c.close()

    result = {"system": "influxdb3-core", "version": version,
              "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "machine": machine(), "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),
              "ingest": ing, "queries": queries, "parquet_after_queries": pf[0] if pf else {}}
    os.makedirs("/results", exist_ok=True)
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    m = result["machine"]
    print(f"\nInfluxDB 3 Core {version} | {ROWS:,} rows, {SERIES:,} series, {len(bodies)} requests of "
          f"{BATCH:,} lines, {WRITERS} writers{', no_sync' if NO_SYNC else ''}")
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})")
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {v['cpus']} CPUs / {v['memory_mib']} MiB" for n, v in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs")
    print(f"\ningest: {ing['rows_per_s']:,} rows/s ({ing['mb_per_s']} MB/s of line protocol) in "
          f"{ing['seconds']} s; request p50 {ing['request_p50_ms']} ms, p99 {ing['request_p99_ms']} ms; "
          f"all queryable {ing['all_visible_after_s']} s after the last ack")
    pq = result["parquet_after_queries"]
    if pq:
        print(f"parquet: {pq.get('files')} files, {(pq.get('bytes') or 0) / 1e6:.1f} MB, "
              f"{pq.get('rows') or 0:,} rows persisted so far\n")
    hdr = f"{'query':<32} {'rows':>7} {'p50 ms':>8} {'p99 ms':>8}"
    print(hdr)
    print("-" * len(hdr))
    for q in queries:
        print(f"{q['query']:<32} {q['rows']:>7,} {q['p50_ms']:>8.2f} {q['p99_ms']:>8.2f}")
    print(f"\nresults/{NAME}.json")


if __name__ == "__main__":
    main()
