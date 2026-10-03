"""`make benchmark`, part 2: parse /results/$NAME.txt (timescaledb-parallel-copy and psql output
from bench/run.sh), print a summary table and write /results/$NAME.json. Standard library only."""

import json
import os
import platform
import re
import statistics
import sys
from datetime import datetime, timezone

NAME = os.environ["NAME"]
RAW = f"/results/{NAME}.txt"
TIME = re.compile(r"^Time: ([\d.]+) ms")
LAYOUTS = {
    "local": "hypertable on the access node only",
    "rf1": "distributed, 3 data nodes, replication_factor 1",
    "rf2": "distributed, 3 data nodes, replication_factor 2",
}


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
    }


def parse(path):
    meta, ingest, queries = {}, {}, {}
    section, layout, query = None, None, None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("=== ingest layout="):
                layout = line.split("=")[-1]
                ingest[layout] = {"layout": layout}
                section = "ingest"
            elif line.startswith("=== queries layout="):
                layout = line.split()[2].split("=")[1]
                section, query = "queries", None
            elif section == "ingest":
                k, _, v = line.partition(": ")
                if k in ("elapsed_ms", "count", "size_bytes"):
                    ingest[layout][k] = int(v)
            elif section == "queries":
                if line.startswith("query: "):
                    query = line[7:]
                elif (m := TIME.match(line)) and query:
                    queries.setdefault(layout, {}).setdefault(query, []).append(float(m[1]))
                    query = None
    for r in ingest.values():
        r["rows_per_s"] = round(r["count"] / r["elapsed_ms"] * 1000) if r.get("elapsed_ms") else 0
    return meta, ingest, queries


def main():
    meta, ingest, queries = parse(RAW)
    if len(ingest) < 3 or len(queries) < 3:
        sys.exit(f"missing ingest or query results in {RAW}")
    med = {lay: {q: statistics.median(t) for q, t in qs.items()} for lay, qs in queries.items()}
    result = {
        "system": "timescaledb-multinode",
        "tool": f"{meta.get('parallel_copy_version', '?')} + psql",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {"server": meta.get("server_version"), "timescaledb": meta.get("timescaledb_version")},
        "parameters": {k: int(meta[k]) for k in ("devices", "days", "rows", "workers", "batch", "runs")},
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "ingest": list(ingest.values()),
        "queries": {lay: [{"query": q, "runs_ms": t, "median_ms": med[lay][q]} for q, t in qs.items()] for lay, qs in queries.items()},
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    p, m = result["parameters"], result["machine"]
    print(f"\n{result['versions']['server']}\nTimescaleDB {result['versions']['timescaledb']} | {result['tool']}")
    print(f"{p['rows']:,} rows = {p['devices']:,} devices x {p['days']} days x 1/min, {p['workers']} workers, batches of {p['batch']}")
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})")
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {c['cpus']} CPUs / {c['memory_mib']} MiB" for n, c in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs")
    hdr = f"\n{'layout':<7} {'rows/s':>9} {'seconds':>8} {'size MiB':>9}   what"
    print(hdr + "\n" + "-" * 80)
    for lay, r in ingest.items():
        print(f"{lay:<7} {r['rows_per_s']:>9,} {r['elapsed_ms'] / 1000:>8.1f} {r['size_bytes'] / 2**20:>9,.0f}   {LAYOUTS[lay]}")
    names = list(med["local"])
    hdr = f"\n{'query (median ms)':<20} " + " ".join(f"{lay:>9}" for lay in ingest)
    print(hdr + "\n" + "-" * (len(hdr) - 1))
    for n in names:
        print(f"{n:<20} " + " ".join(f"{med[lay][n]:>9.1f}" for lay in ingest))
    print(f"\nraw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
