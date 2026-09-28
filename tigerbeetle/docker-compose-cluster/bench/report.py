"""`make benchmark`, part 2: parse /results/$NAME.txt (written by bench/run.sh), print a
summary table and write /results/$NAME.json. Standard library only."""

import json
import os
import platform
import re
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
    }


def parse(path):
    """`tigerbeetle benchmark` prints two blocks on stdout:
        13 batches in 0.10 s / transfer batch size = 8189 txs / load accepted = 974163 tx/s /
        batch latency p1|p50|p99|p100 = N ms
        100 queries in 0.0 s / query latency p1|p50|p99|p100 = N ms"""
    meta, res = {}, {"transfers": {}, "queries": {}}
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("=== command: "):
                res["command"] = line[len("=== command: ") :]
            elif m := re.fullmatch(r"(\d+) batches in ([\d.]+) s", line):
                res["transfers"].update(batches=int(m[1]), seconds=float(m[2]))
            elif m := re.fullmatch(r"(\d+) queries in ([\d.]+) s", line):
                res["queries"].update(count=int(m[1]), seconds=float(m[2]))
            elif m := re.fullmatch(r"transfer batch size = (\d+) txs", line):
                res["transfers"]["batch_size"] = int(m[1])
            elif m := re.fullmatch(r"load accepted = (\d+) tx/s", line):
                res["transfers"]["transfers_per_s"] = int(m[1])
            elif m := re.fullmatch(r"(batch|query) latency (p\d+)\s*= (\d+) ms", line):
                res["transfers" if m[1] == "batch" else "queries"][f"{m[2]}_ms"] = int(m[3])
    return meta, res


def main():
    meta, res = parse(RAW)
    t, q = res["transfers"], res["queries"]
    if "transfers_per_s" not in t:
        sys.exit(f"no benchmark result in {RAW}")
    result = {
        "system": "tigerbeetle",
        "tool": f"tigerbeetle benchmark {meta.get('tigerbeetle_version', '?')}",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {"tigerbeetle": meta.get("tigerbeetle_version")},
        "cluster": {"replicas": int(meta["replicas"]), "addresses": meta["addresses"]},
        "parameters": {k: int(meta[k]) for k in ("transfers", "accounts", "clients", "batch")},
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "command": res.get("command"),
        "create_transfers": t,
        "get_account_transfers": q,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    p, m = result["parameters"], result["machine"]
    print(
        f"\nTigerBeetle {result['versions']['tigerbeetle']}, {result['cluster']['replicas']} replica(s) | "
        f"tigerbeetle benchmark | {p['transfers']:,} transfers, {p['accounts']:,} accounts, "
        f"{p['clients']} client(s), batches of up to {p['batch']:,}"
    )
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})")
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {c['cpus']} CPUs / {c['memory_mib']} MiB" for n, c in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs\n")
    else:
        print()
    hdr = f"{'operation':<22} {'count':>10} {'seconds':>8} {'per s':>11} {'p50 ms':>7} {'p99 ms':>7} {'p100 ms':>8}"
    print(hdr)
    print("-" * len(hdr))
    print(
        f"{'create_transfers':<22} {p['transfers']:>10,} {t.get('seconds', 0):>8.2f} {t['transfers_per_s']:>11,} "
        f"{t.get('p50_ms', 0):>7} {t.get('p99_ms', 0):>7} {t.get('p100_ms', 0):>8}"
    )
    print(f"{'  (latency per batch of ' + str(t.get('batch_size', '?')) + ')':<22}")
    if q:
        qps = round(q["count"] / q["seconds"]) if q.get("seconds") else 0
        print(
            f"{'get_account_transfers':<22} {q.get('count', 0):>10,} {q.get('seconds', 0):>8.2f} "
            f"{f'{qps:,}' if qps else '-':>11} {q.get('p50_ms', 0):>7} {q.get('p99_ms', 0):>7} {q.get('p100_ms', 0):>8}"
        )
    print(f"\nraw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
