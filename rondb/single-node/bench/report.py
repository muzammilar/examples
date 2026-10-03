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


def percentile(hist, p):
    """p-th percentile (ms) from sysbench's --histogram lines [(value_ms, count)]."""
    total = sum(c for _, c in hist)
    seen = 0
    for v, c in hist:
        seen += c
        if seen >= total * p / 100:
            return v
    return None


def finish_sysbench(w):
    hist = w.pop("_hist", [])
    if hist:
        w["p50_ms"] = percentile(hist, 50)


def parse(path):
    meta, workloads, cur = {}, [], None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            s = line.strip()
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("prepare: seconds="):
                meta["prepare_s"] = float(line.split("=", 1)[1])
            elif line.startswith("=== workload "):
                if cur and cur["tool"] == "sysbench":
                    finish_sysbench(cur)
                name, _, cmd = line[len("=== workload ") :].partition(": ")
                cur = {"name": name, "tool": cmd.split()[0], "command": cmd, "_hist": []}
                workloads.append(cur)
            elif cur is None:
                continue
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
    if cur and cur["tool"] == "sysbench":
        finish_sysbench(cur)
    for w in workloads:
        w.pop("_hist", None)
        w.setdefault("errors", 0)
    return meta, workloads


def main():
    meta, workloads = parse(RAW)
    if not workloads:
        sys.exit(f"no workloads in {RAW}")
    result = {
        "system": "rondb",
        "tool": f"sysbench {meta.get('sysbench_version', '?')} (debian:trixie-slim)",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {
            "server": meta.get("server_version"),
            "sysbench": meta.get("sysbench_version"),
        },
        "cluster": {
            "data_nodes": int(meta.get("data_nodes", 0)),
            "no_of_replicas": int(meta.get("no_of_replicas", 0)),
            "data_memory_per_node_mib": round(int(meta.get("data_memory_bytes", 0)) / 2**20),
            "engine": "ndbcluster",
        },
        "parameters": {
            "duration_s": int(meta["duration_s"]),
            "threads": int(meta["threads"]),
            "tables": int(meta["tables"]),
            "table_size": int(meta["table_size"]),
        },
        "prepare_s": meta.get("prepare_s"),
        "machine": machine(),
        "workloads": workloads,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    c, p, m = result["cluster"], result["parameters"], result["machine"]
    print(
        f"\nRonDB {result['versions']['server']}, {c['data_nodes']} data nodes, NoOfReplicas={c['no_of_replicas']}, "
        f"ENGINE=NDB | {result['tool']} | {p['threads']} threads, {p['tables']} x {p['table_size']:,} rows, "
        f"{p['duration_s']} s per workload (load: {result['prepare_s']} s)"
    )
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})")
    print()
    hdr = f"{'workload':<18} {'tps':>9} {'qps':>10} {'avg ms':>8} {'p50 ms':>8} {'p99 ms':>8} {'errors':>7}"
    print(hdr)
    print("-" * len(hdr))
    for w in workloads:
        tps = f"{w['tps']:,.0f}" if "tps" in w else "-"
        avg = f"{w['avg_ms']:.2f}" if "avg_ms" in w else "-"
        print(
            f"{w['name']:<18} {tps:>9} {w.get('qps', 0):>10,.0f} {avg:>8} {w.get('p50_ms', 0):>8.2f} "
            f"{w.get('p99_ms', 0):>8.2f} {w['errors']:>7}"
        )
    print("\ntps = sysbench transactions; queries per transaction: point_select 1, read_only 16, "
          "read_write 20 (incl. BEGIN/COMMIT)\nerrors = sysbench's ignored (retried) errors")
    print(f"raw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
