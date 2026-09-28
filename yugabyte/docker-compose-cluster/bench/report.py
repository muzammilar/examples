"""`make benchmark`, part 2: parse /results/$NAME.txt (ysql_bench output from bench/run.sh), print a
summary table and write /results/$NAME.json. Standard library only."""

import json
import os
import platform
import re
import sys
from datetime import datetime, timezone

NAME = os.environ["NAME"]
RAW = f"/results/{NAME}.txt"

# ysql_bench summary lines -> JSON keys, summed over the per-node processes of a run
SUMMARY = {
    "number of transactions actually processed": "transactions",
    "number of failed transactions": "failed",
    "number of transactions retried": "retried",
    "total number of retries": "retries",
}
TPS = re.compile(r"^tps = ([\d.]+)")


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
    meta, runs, cur = {}, [], None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("=== run "):
                head, _, cmd = line[len("=== run ") :].partition(": ")
                name, _, clients = head.partition(" clients=")
                cur = {"workload": name, "clients": int(clients), "command": cmd, "tps": 0.0, "nodes": []}
                runs.append(cur)
            elif line.startswith("=== "):
                cur = None
            elif cur is None:
                continue
            elif line.startswith("--- node "):
                cur["nodes"].append(line[9:])
            elif m := TPS.match(line):
                cur["tps"] += float(m[1])
            elif line.startswith("latency: "):
                for kv in line[9:].split():
                    k, _, v = kv.partition("=")
                    cur["latency_samples" if k == "n" else k] = int(v) if k == "n" else float(v)
            else:
                k, _, v = line.partition(": ")
                if k in SUMMARY:
                    cur[SUMMARY[k]] = cur.get(SUMMARY[k], 0) + int(v.split()[0])
    return meta, runs


def main():
    meta, runs = parse(RAW)
    if not runs or not all(r["tps"] for r in runs):
        sys.exit(f"missing ysql_bench results in {RAW}")
    result = {
        "system": "yugabytedb",
        "tool": f"{meta.get('ysql_bench_version', 'ysql_bench ?')} (yugabytedb/yugabyte image)",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {"server": meta.get("server_version"), "ysql_bench": meta.get("ysql_bench_version")},
        "cluster": {
            "nodes": meta.get("nodes", "").split(),
            "tservers_alive": int(meta.get("tservers_alive", 0)),
            "replication_factor": int(meta.get("replication_factor", 0)),
            "isolation": meta.get("isolation"),
        },
        "parameters": {
            "duration_s": int(meta["duration_s"]),
            "clients": [int(c) for c in meta["clients"].split()],
            "scale": int(meta["scale"]),
            "max_tries": int(meta["max_tries"]),
        },
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "ysql": runs,
        "ycql": "skipped: the image ships neither cassandra-stress nor yb-sample-apps (and no JRE)",
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    c, p, m = result["cluster"], result["parameters"], result["machine"]
    print(
        f"\n{result['versions']['server'].split(' on ')[0]} | {len(c['nodes'])} nodes, {c['tservers_alive']} tservers alive, "
        f"RF={c['replication_factor']}, isolation {c['isolation']} | {result['tool']}"
    )
    print(
        f"scale {p['scale']} ({p['scale'] * 100_000:,} accounts), {p['duration_s']} s per run, clients split over "
        f"{', '.join(c['nodes'])} | Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})"
    )
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {c['cpus']} CPUs / {c['memory_mib']} MiB" for n, c in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs")
    print()
    hdr = f"{'workload':<12} {'clients':>7} {'tps':>9} {'avg ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'failed':>7} {'retried':>8}"
    print(hdr)
    print("-" * len(hdr))
    for r in runs:
        print(
            f"{r['workload']:<12} {r['clients']:>7} {r['tps']:>9,.0f} {r.get('avg_ms', 0):>8.2f} "
            f"{r.get('p95_ms', 0):>8.2f} {r.get('p99_ms', 0):>8.2f} {r.get('failed', 0):>7} {r.get('retried', 0):>8}"
        )
    print(f"\nYCQL: {result['ycql']}")
    print(f"raw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
