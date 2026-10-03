"""`make benchmark`, part 2: parse /results/$NAME.txt (timescaledb-parallel-copy, psql and pgbench
output from bench/run.sh), print a summary table and write /results/$NAME.json. Standard library only."""

import json
import os
import platform
import re
import sys
from datetime import datetime, timezone

NAME = os.environ["NAME"]
RAW = f"/results/{NAME}.txt"
PGBENCH = {
    "tps": re.compile(r"^tps = ([\d.]+)"),
    "latency_avg_ms": re.compile(r"^latency average = ([\d.]+) ms"),
    "transactions": re.compile(r"^number of transactions actually processed: (\d+)"),
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
    meta, ingest, reads = {}, [], []
    section, cur = None, None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("=== ingest "):
                cur = dict(kv.split("=") for kv in line.split()[2:])
                ingest.append(cur)
                section = "ingest"
            elif line.startswith("=== reads "):
                cur = {"target": line.split("target=")[1]}
                reads.append(cur)
                section = "reads"
            elif section == "ingest":
                k, _, v = line.partition(": ")
                if k in ("elapsed_ms", "replica_catchup_ms", "wal_bytes", "count", "size_bytes"):
                    cur[k] = int(float(v))
            elif section == "reads":
                for k, rx in PGBENCH.items():
                    if m := rx.match(line):
                        cur[k] = float(m[1])
    for r in ingest:
        r["rows_per_s"] = round(r["count"] / r["elapsed_ms"] * 1000) if r.get("elapsed_ms") else 0
    return meta, ingest, reads


def main():
    meta, ingest, reads = parse(RAW)
    if not ingest or not all(r.get("count") for r in ingest) or not all(r.get("tps") for r in reads):
        sys.exit(f"missing ingest or read results in {RAW}")
    result = {
        "system": "timescaledb",
        "setup": "3 Patroni nodes (1 primary + 2 streaming replicas), etcd x3, HAProxy",
        "tool": f"timescaledb-parallel-copy {meta.get('parallel_copy_version', '?')} + pgbench",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {
            "server": meta.get("server_version"),
            "timescaledb": meta.get("timescaledb_version"),
            "patroni": meta.get("patroni_version"),
        },
        "parameters": {k: int(meta[k]) for k in ("devices", "days", "rows", "workers", "batch", "clients", "duration_s")},
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "ingest": ingest,
        "reads": reads,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    p, m, v = result["parameters"], result["machine"], result["versions"]
    print(f"\n{v['server']}\nTimescaleDB {v['timescaledb']}, Patroni {v['patroni']} | {result['tool']}")
    print(
        f"{p['rows']:,} rows = {p['devices']:,} devices x {p['days']} days x 1/min, "
        f"{p['workers']} workers, batches of {p['batch']}"
    )
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})")
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {c['cpus']} CPUs / {c['memory_mib']} MiB" for n, c in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs")
    mib = 2**20
    hdr = f"\n{'replication':<12} {'ingest into':<12} {'rows/s':>10} {'seconds':>8} {'WAL MiB':>8} {'size MiB':>9} {'replicas caught up ms':>22}"
    print(hdr + "\n" + "-" * (len(hdr) - 1))
    for r in ingest:
        print(
            f"{r['mode']:<12} {r['store']:<12} {r['rows_per_s']:>10,} {r['elapsed_ms'] / 1000:>8.1f} "
            f"{r['wal_bytes'] / mib:>8,.0f} {r['size_bytes'] / mib:>9,.0f} {r['replica_catchup_ms']:>22,}"
        )
    hdr = f"\n{'reads on':<10} {'clients':>7} {'queries/s':>10} {'avg ms':>8}"
    print(hdr + "\n" + "-" * (len(hdr) - 1))
    for r in reads:
        print(f"{r['target']:<10} {p['clients']:>7} {r['tps']:>10,.0f} {r['latency_avg_ms']:>8.2f}")
    print(f"\nraw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
