"""`make benchmark`, part 2: parse /results/$NAME.txt (pgbench and psql output from bench/run.sh),
print a summary table and write /results/$NAME.json. Standard library only."""

import json
import os
import platform
import re
import statistics
import sys
from datetime import datetime, timezone

NAME = os.environ["NAME"]
RAW = f"/results/{NAME}.txt"

# pgbench summary lines -> JSON keys
PGBENCH = {
    "number of transactions actually processed": "transactions",
    "number of failed transactions": "failed",
    "number of transactions retried": "retried",
    "total number of retries": "retries",
}
TPS = re.compile(r"^tps = ([\d.]+)")
TIME = re.compile(r"^Time: ([\d.]+) ms")


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
    meta, runs, queries, load = {}, [], {}, {}
    section, cur, query, last_insert = None, None, None, None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("=== run "):
                head, _, cmd = line[len("=== run ") :].partition(": ")
                name, _, clients = head.partition(" clients=")
                cur = {"workload": name, "clients": int(clients), "command": cmd, "tps": 0.0}
                runs.append(cur)
                section = "run"
            elif line.startswith("=== load"):
                section = "load"
            elif line.startswith("=== "):
                section, query = line[4:].split()[0], None
            elif section == "run":
                if m := TPS.match(line):
                    cur["tps"] += float(m[1])
                elif line.startswith("latency: "):
                    for kv in line[9:].split():
                        k, _, v = kv.partition("=")
                        cur["latency_samples" if k == "n" else k] = float(v)
                else:
                    k, _, v = line.partition(": ")
                    if k in PGBENCH:
                        cur[PGBENCH[k]] = cur.get(PGBENCH[k], 0) + int(v.split()[0])
            elif section == "load":
                if line.startswith("INSERT 0 "):
                    last_insert = int(line.split()[2])
                elif (m := TIME.match(line)) and last_insert:
                    load[{100000: "customers_100k_ms", 3000000: "orders_3m_ms"}.get(last_insert, f"insert_{last_insert}_ms")] = float(m[1])
                    last_insert = None
            elif section == "analytics":
                if line.startswith("query: "):
                    query = line[7:]
                elif (m := TIME.match(line)) and query:
                    queries.setdefault(query, []).append(float(m[1]))
                    query = None
    for r in runs:
        r["latency_samples"] = int(r.get("latency_samples", 0))
    return meta, runs, queries, load


def main():
    meta, runs, queries, load = parse(RAW)
    if not runs or not all(r["tps"] for r in runs) or not queries:
        sys.exit(f"missing pgbench or analytic results in {RAW}")
    analytics = [
        {"query": q, "runs_ms": t, "min_ms": min(t), "median_ms": statistics.median(t)} for q, t in queries.items()
    ]
    result = {
        "system": "cedardb",
        "tool": f"pgbench {meta.get('pgbench_version', '?')} + psql (postgres:17-alpine)",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {"server": meta.get("server_version"), "pgbench": meta.get("pgbench_version")},
        "server": {"isolation": meta.get("isolation")},
        "parameters": {
            "duration_s": int(meta["duration_s"]),
            "clients": [int(c) for c in meta["clients"].split()],
            "scale": int(meta["scale"]),
            "max_tries": int(meta["max_tries"]),
            "analytic_runs": int(meta["analytic_runs"]),
            "analytic_rows": {"customers": 100000, "orders": 3000000},
        },
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "oltp": runs,
        "load": load,
        "analytics": analytics,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    p, m = result["parameters"], result["machine"]
    print(
        f"\n{result['versions']['server']} | {result['tool']} | scale {p['scale']} "
        f"({p['scale'] * 100_000:,} accounts), {p['duration_s']} s per run, isolation {result['server']['isolation']}"
    )
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})")
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {c['cpus']} CPUs / {c['memory_mib']} MiB" for n, c in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs\n")
    else:
        print()
    hdr = f"{'workload':<12} {'clients':>7} {'tps':>10} {'avg ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'failed':>7} {'retried':>8}"
    print(hdr)
    print("-" * len(hdr))
    for r in runs:
        print(
            f"{r['workload']:<12} {r['clients']:>7} {r['tps']:>10,.0f} {r.get('avg_ms', 0):>8.2f} "
            f"{r.get('p95_ms', 0):>8.2f} {r.get('p99_ms', 0):>8.2f} {r.get('failed', 0):>7} {r.get('retried', 0):>8}"
        )
    print("\nload: " + ", ".join(f"{k.removesuffix('_ms')} {v / 1000:.2f} s" for k, v in load.items()))
    hdr = f"{'analytic query (3M orders)':<28} {'min ms':>9} {'median ms':>10}"
    print("\n" + hdr)
    print("-" * len(hdr))
    for a in analytics:
        print(f"{a['query']:<28} {a['min_ms']:>9.1f} {a['median_ms']:>10.1f}")
    print(f"\nraw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
