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
# go-ycsb's result fields -> JSON keys (latencies in microseconds)
KEYS = {"Takes(s)": "seconds", "Count": "count", "OPS": "ops", "Avg(us)": "avg_us", "Min(us)": "min_us",
        "Max(us)": "max_us", "50th(us)": "p50_us", "90th(us)": "p90_us", "95th(us)": "p95_us",
        "99th(us)": "p99_us", "99.9th(us)": "p999_us"}


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
    meta, loads, workloads, cur, done = {}, {}, [], None, False
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("=== "):
                kind, _, rest = line[4:].partition(" ")
                name, _, cmd = rest.partition(": ")
                cur = {"name": name, "api": name.split("_")[0], "command": cmd, "ops": {}}
                if kind == "workload":
                    workloads.append(cur)
                else:
                    loads[name] = cur
                done = False
            elif m := re.match(r"load: (\w+) seconds=([\d.]+)", line):
                loads[m[1]]["wall_s"] = float(m[2])
            elif line.startswith("Run finished"):
                done = True
            # final totals (after "Run finished"): one line per operation type, <OP>_ERROR for failures
            elif done and cur and (m := re.match(r"(\w+)\s+- (Takes\(s\): .*)", line)):
                fields = dict(kv.split(": ", 1) for kv in m[2].split(", "))
                cur["ops"][m[1]] = {KEYS[k]: float(v) for k, v in fields.items() if k in KEYS}
    for w in [*loads.values(), *workloads]:
        w["errors"] = int(sum(o.get("count", 0) for k, o in w["ops"].items() if k.endswith("_ERROR")))
    return meta, loads, workloads


def main():
    meta, loads, workloads = parse(RAW)
    if not workloads or any(not w["ops"] for w in workloads):
        sys.exit(f"missing go-ycsb results in {RAW}")
    result = {
        "system": "tikv",
        "tool": f"go-ycsb {meta.get('go_ycsb_version', '?')} (tikv driver, built from source)",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {
            "tikv": meta.get("tikv_version"),
            "pd": meta.get("pd_version"),
            "go_ycsb": meta.get("go_ycsb_version"),
        },
        "cluster": {
            "pd": int(meta.get("pd_members", 0)),
            "tikv_stores_up": int(meta.get("tikv_stores_up", 0)),
            "max_replicas": int(meta.get("max_replicas", 0)),
        },
        "parameters": {
            "threads": int(meta["threads"]),
            "records": int(meta["records"]),
            "operations": int(meta["operations"]),
            "fields": "10 x 100 bytes",
            "request_distribution": "uniform",
        },
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "loads": loads,
        "workloads": workloads,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    c, p, m, v = result["cluster"], result["parameters"], result["machine"], result["versions"]
    print(
        f"\nTiKV {v['tikv']} (PD {v['pd']}): {c['pd']} PD, {c['tikv_stores_up']} stores, max-replicas "
        f"{c['max_replicas']} | {result['tool']} | {p['threads']} threads, {p['records']:,} records, "
        f"{p['operations']:,} operations per workload"
    )
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})")
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {c['cpus']} CPUs / {c['memory_mib']} MiB" for n, c in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs\n")
    else:
        print()
    hdr = f"{'workload':<10} {'op':<7} {'count':>8} {'ops/s':>9} {'avg ms':>8} {'p50 ms':>8} {'p99 ms':>8} {'errors':>7}"
    print(hdr)
    print("-" * len(hdr))
    for w in [*loads.values(), *workloads]:
        label = f"{w['name']} load" if w in loads.values() else w["name"]
        # READ / UPDATE / INSERT first, TOTAL (all operation types together) last, only for mixes
        for op, o in sorted(w["ops"].items(), key=lambda kv: (kv[0] == "TOTAL", kv[0])):
            if op.endswith("_ERROR") or (op == "TOTAL" and len(w["ops"]) <= 2):
                continue
            err = w["ops"].get(f"{op}_ERROR", {}).get("count", 0)
            print(f"{label:<10} {op:<7} {o.get('count', 0):>8,.0f} {o.get('ops', 0):>9,.0f} "
                  f"{o.get('avg_us', 0) / 1e3:>8.2f} {o.get('p50_us', 0) / 1e3:>8.2f} "
                  f"{o.get('p99_us', 0) / 1e3:>8.2f} {err:>7,.0f}")
    print("\nraw = RawKV (tikv.type=raw), txn = TxnKV (tikv.type=txn: timestamp from PD per read, "
          "Percolator commit per update)\na = YCSB workload A (50% read / 50% update), c = workload C "
          "(100% read); errors = failed operations (<OP>_ERROR,\n"
          "for txn updates: write conflicts between concurrent transactions on the same key, not retried)")
    print(f"raw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
