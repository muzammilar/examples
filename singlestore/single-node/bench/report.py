"""`make benchmark`, part 2: parse /results/$NAME.txt (written by bench/run.sh), print a
summary table and write /results/$NAME.json. Standard library only."""

import json
import os
import platform
import re
import statistics
import sys
from datetime import datetime, timezone

NAME = os.environ["NAME"]
RAW = f"/results/{NAME}.txt"


def machine():
    mem_kb = 0
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                mem_kb = int(line.split()[1])
    return {
        "docker_vm_cpus": os.cpu_count(),
        "docker_vm_memory_gib": round(mem_kb / 2**20, 1),
        "docker_vm_kernel": platform.release(),
        "arch": platform.machine(),
        "host": os.environ.get("HOST_INFO", ""),
        "docker": os.environ.get("DOCKER_INFO", ""),
        # the Dev Image is amd64 only: on an arm64 host it runs under emulation (Rosetta)
        "server_platform": os.environ.get("SERVER_PLATFORM", ""),
    }


def percentile(hist, p):
    """p-th percentile (ms) from sysbench's --histogram lines [(value_ms, count)]."""
    total = sum(c for _, c in hist)
    seen = 0
    for v, c in hist:
        seen += c
        if seen >= total * p / 100:
            return v
    return None


def parse(path):
    meta, workloads, queries, cur = {}, [], [], None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            s = line.strip()
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("prepare: sysbench seconds="):
                meta["prepare_s"] = float(line.split("=", 1)[1])
            elif line.startswith("=== workload "):
                name, _, cmd = line[len("=== workload ") :].partition(": ")
                cur = {"name": name, "tool": "sysbench", "command": cmd, "_hist": []}
                workloads.append(cur)
            elif line.startswith("=== analytics"):
                cur = None
            elif m := re.match(r"query (\w+): rows=(\d+) ms=([\d.,]+)", line):
                runs = [float(x) for x in m[3].split(",")]
                warm = runs[1:] or runs
                queries.append({"name": m[1], "rows": int(m[2]), "runs_ms": runs, "first_ms": runs[0],
                                "warm_median_ms": round(statistics.median(warm), 2), "warm_min_ms": min(warm)})
            elif m := re.match(r"query_sql (\w+): (.*)", line):
                next(q for q in queries if q["name"] == m[1])["sql"] = m[2]
            elif cur is None:
                continue
            elif m := re.fullmatch(r"\s*([\d.]+) \|\**\s+(\d+)", line):
                cur["_hist"].append((float(m[1]), int(m[2])))
            elif m := re.match(r"transactions:\s+(\d+)\s+\(([\d.]+) per sec", s):
                cur["transactions"], cur["tps"] = int(m[1]), float(m[2])
            elif m := re.match(r"queries:\s+(\d+)\s+\(([\d.]+) per sec", s):
                cur["queries"], cur["qps"] = int(m[1]), float(m[2])
            elif m := re.match(r"ignored errors:\s+(\d+)", s):
                cur["errors"] = int(m[1])
            elif m := re.match(r"(min|avg|max|99th percentile):\s+([\d.]+)$", s):
                cur[{"99th percentile": "p99"}.get(m[1], m[1]) + "_ms"] = float(m[2])
    for w in workloads:
        hist = w.pop("_hist", [])
        if hist:
            w["p50_ms"] = percentile(hist, 50)
        w.setdefault("errors", 0)
    return meta, workloads, queries


def main():
    meta, workloads, queries = parse(RAW)
    if not workloads or not queries:
        sys.exit(f"no workloads or analytics queries in {RAW}")
    result = {
        "system": "singlestore",
        "tool": f"sysbench {meta.get('sysbench_version', '?')} (debian:trixie-slim)",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {
            "server": meta.get("server_version"),
            "mysql_compat": meta.get("mysql_version"),
            "dev_image": os.environ.get("DEV_IMAGE", ""),
            "sysbench": meta.get("sysbench_version"),
        },
        "cluster": {
            "aggregators": 1,
            "leaves": int(meta.get("leaves", 0)),
            "partitions_per_database": int(meta.get("partitions", 0)),
        },
        "parameters": {
            "duration_s": int(meta["duration_s"]),
            "threads": int(meta["threads"]),
            "tables": int(meta["tables"]),
            "table_size": int(meta["table_size"]),
            "table_type": meta["table_type"],
            "rand_type": meta["rand_type"],
            "sbtest_storage_type": meta.get("sbtest_storage_type"),
            "analytics_runs": int(meta["runs"]),
            "orders_rows": int(meta.get("orders_rows", 0)),
        },
        "prepare_s": meta.get("prepare_s"),
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "workloads": workloads,
        "analytics": queries,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    c, p, m, v = result["cluster"], result["parameters"], result["machine"], result["versions"]
    print(
        f"\nSingleStore {v['server']} (Dev Image {v['dev_image']}), 1 aggregator + {c['leaves']} leaf, "
        f"{c['partitions_per_database']} partitions | {result['tool']} | {p['threads']} threads, "
        f"{p['duration_s']} s per workload"
    )
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']}); "
          f"server image {m['server_platform']}")
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {c['cpus']} CPUs / {c['memory_mib']} MiB" for n, c in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs")
    if m["arch"] in ("aarch64", "arm64"):
        print("NOTE: the amd64 server runs under emulation on this arm64 machine; the numbers are not "
              "representative of SingleStore on x86-64")
    print(f"\nsysbench OLTP, {p['tables']} x {p['table_size']:,} rows, {p['sbtest_storage_type']}, "
          f"--rand-type={p['rand_type']} (load: {result['prepare_s']} s)")
    hdr = f"{'workload':<18} {'tps':>9} {'qps':>10} {'avg ms':>8} {'p50 ms':>8} {'p99 ms':>8} {'errors':>7}"
    print(hdr)
    print("-" * len(hdr))
    for w in workloads:
        print(
            f"{w['name']:<18} {w.get('tps', 0):>9,.0f} {w.get('qps', 0):>10,.0f} {w.get('avg_ms', 0):>8.2f} "
            f"{w.get('p50_ms', 0):>8.2f} {w.get('p99_ms', 0):>8.2f} {w['errors']:>7}"
        )
    print(f"\ncolumnstore analytics on demo.orders ({p['orders_rows']:,} rows), {p['analytics_runs']} runs each")
    hdr = f"{'query':<16} {'rows':>5} {'first ms':>9} {'warm median ms':>15} {'warm min ms':>12}"
    print(hdr)
    print("-" * len(hdr))
    for q in queries:
        print(f"{q['name']:<16} {q['rows']:>5} {q['first_ms']:>9.1f} {q['warm_median_ms']:>15.1f} {q['warm_min_ms']:>12.1f}")
    print("\ntps = sysbench transactions; queries per transaction: point_select 1, read_only 16, "
          "read_write 20 (incl. BEGIN/COMMIT); errors = sysbench's ignored (retried) errors\n"
          "first = first run of the query shape (may include plan compilation), warm = the other runs")
    print(f"raw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
