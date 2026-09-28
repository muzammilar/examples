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
# go-tpc's [Summary] fields -> JSON keys
TPC_KEYS = {"Takes(s)": "seconds", "Count": "count", "TPM": "tpm", "Avg(ms)": "avg_ms",
            "50th(ms)": "p50_ms", "95th(ms)": "p95_ms", "99th(ms)": "p99_ms", "Max(ms)": "max_ms"}


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
    meta, prepare, workloads, cur = {}, {}, [], None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            s = line.strip()
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif m := re.match(r"prepare: (\w+) seconds=([\d.]+)", line):
                prepare[m[1]] = float(m[2])
            elif line.startswith("=== workload "):
                name, _, cmd = line[len("=== workload ") :].partition(": ")
                cur = {"name": name, "tool": cmd.split()[0], "command": cmd, "_hist": []}
                if cur["tool"] == "go-tpc":
                    cur["transactions"] = {}
                workloads.append(cur)
            elif cur is None:
                continue
            # go-tpc: one [Summary] line per transaction type (and <TYPE>_ERR for failed ones)
            elif m := re.match(r"\[Summary\] (\w+) - (.*)", s):
                fields = dict(kv.split(": ", 1) for kv in m[2].split(", "))
                cur["transactions"][m[1]] = {TPC_KEYS[k]: float(v) for k, v in fields.items() if k in TPC_KEYS}
            elif m := re.match(r"tpmC: ([\d.]+), tpmTotal: ([\d.]+)", s):
                cur["tpmC"], cur["tpm_total"] = float(m[1]), float(m[2])
            # sysbench
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
        if w["tool"] == "go-tpc":
            txns = w["transactions"]
            w["errors"] = int(sum(t.get("count", 0) for k, t in txns.items() if k.endswith("_ERR")))
        w.setdefault("errors", 0)
    return meta, prepare, workloads


def main():
    meta, prepare, workloads = parse(RAW)
    if not workloads:
        sys.exit(f"no workloads in {RAW}")
    tpcc = next((w for w in workloads if w["name"] == "tpcc"), None)
    if not tpcc or "tpmC" not in tpcc:
        sys.exit(f"no tpmC in {RAW}")
    result = {
        "system": "tidb",
        "tool": f"go-tpc {meta.get('go_tpc_version', '?')}, sysbench {meta.get('sysbench_version', '?')} (debian:trixie-slim)",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {
            "tidb": meta.get("tidb_version"),
            "server_version": meta.get("server_version"),
            "go_tpc": meta.get("go_tpc_version"),
            "sysbench": meta.get("sysbench_version"),
        },
        "cluster": {
            "pd": int(meta.get("pd_members", 0)),
            "tikv_stores_up": int(meta.get("tikv_stores_up", 0)),
            "tidb": 1,
            "max_replicas": int(meta.get("max_replicas", 0)),
        },
        "parameters": {
            "duration_s": int(meta["duration_s"]),
            "threads": int(meta["threads"]),
            "warehouses": int(meta["warehouses"]),
            "tables": int(meta["tables"]),
            "table_size": int(meta["table_size"]),
        },
        "prepare_s": prepare,
        "machine": machine(),
        "workloads": workloads,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    c, p, m, v = result["cluster"], result["parameters"], result["machine"], result["versions"]
    print(
        f"\nTiDB {v['tidb']}: {c['pd']} PD, {c['tikv_stores_up']} TiKV (max-replicas {c['max_replicas']}), 1 TiDB | "
        f"{result['tool']} | {p['threads']} threads, {p['duration_s']} s per workload"
    )
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})\n")

    print(f"TPC-C, {p['warehouses']} warehouse(s) (load: {prepare.get('tpcc')} s): "
          f"tpmC {tpcc['tpmC']:,.0f} (new-order/min), tpmTotal {tpcc['tpm_total']:,.0f}")
    hdr = f"{'transaction':<14} {'count':>8} {'tpm':>9} {'avg ms':>8} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'errors':>7}"
    print(hdr)
    print("-" * len(hdr))
    txns = tpcc["transactions"]
    for name, t in txns.items():
        if name.endswith("_ERR"):
            continue
        err = txns.get(f"{name}_ERR", {}).get("count", 0)
        print(f"{name:<14} {t.get('count', 0):>8,.0f} {t.get('tpm', 0):>9,.0f} {t.get('avg_ms', 0):>8.1f} "
              f"{t.get('p50_ms', 0):>8.1f} {t.get('p95_ms', 0):>8.1f} {t.get('p99_ms', 0):>8.1f} {err:>7,.0f}")

    print(f"\nsysbench, {p['tables']} x {p['table_size']:,} rows (load: {prepare.get('sysbench')} s)")
    hdr = f"{'workload':<18} {'tps':>9} {'qps':>10} {'avg ms':>8} {'p50 ms':>8} {'p99 ms':>8} {'errors':>7}"
    print(hdr)
    print("-" * len(hdr))
    for w in workloads:
        if w["tool"] != "sysbench":
            continue
        print(
            f"{w['name']:<18} {w.get('tps', 0):>9,.0f} {w.get('qps', 0):>10,.0f} {w.get('avg_ms', 0):>8.2f} "
            f"{w.get('p50_ms', 0):>8.2f} {w.get('p99_ms', 0):>8.2f} {w['errors']:>7}"
        )
    print("\ntpmC = NEW_ORDER transactions/min; errors = failed TPC-C transactions (<TYPE>_ERR) and "
          "sysbench's ignored (retried) errors\nqueries per sysbench transaction: point_select 1, "
          "read_write 20 (incl. BEGIN/COMMIT)")
    print(f"raw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
