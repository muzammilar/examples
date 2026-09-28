"""`make benchmark`, part 2: parse /results/$NAME.txt (cassandra-stress output from bench/run.sh),
print a summary table and write /results/$NAME.json. Standard library only."""

import json
import os
import platform
import re
import sys
from datetime import datetime, timezone

NAME = os.environ["NAME"]
RAW = f"/results/{NAME}.txt"

# lines of cassandra-stress's final "Results:" block -> JSON keys (per op type, from the [...] part)
METRICS = {
    "Op rate": "ops_per_s",
    "Latency mean": "mean_ms",
    "Latency median": "p50_ms",
    "Latency 95th percentile": "p95_ms",
    "Latency 99th percentile": "p99_ms",
    "Latency 99.9th percentile": "p99.9_ms",
    "Latency max": "max_ms",
    "Total partitions": "partitions",
    "Total errors": "errors",
}
LINE = re.compile(r"^(%s)\s*:\s*[\d.,]+.*?\[(.*)\]\s*$" % "|".join(map(re.escape, METRICS)))
PER_OP = re.compile(r"(\w+):\s*([\d.,]+)")


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


def number(s):
    s = s.replace(",", "")
    return float(s) if "." in s else int(s)


def parse(path):
    meta, workloads, cur, in_results = {}, [], None, False
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("=== workload "):
                name, _, cmd = line[len("=== workload ") :].partition(": ")
                cur, in_results = {"name": name, "command": cmd, "ops": {}}, False
                workloads.append(cur)
            elif cur is None:
                continue
            elif line.startswith("Results:"):
                in_results = True
            elif in_results and line.startswith("Total operation time"):
                cur["duration"] = line.split(":", 1)[1].strip()
            elif in_results and (m := LINE.match(line)):
                key = METRICS[m[1]]
                for op, v in PER_OP.findall(m[2]):
                    cur["ops"].setdefault(op.lower(), {})[key] = number(v)
    for w in workloads:
        w["total_ops_per_s"] = sum(o.get("ops_per_s", 0) for o in w["ops"].values())
    return meta, workloads


def main():
    meta, workloads = parse(RAW)
    if not workloads or not all(w["ops"] for w in workloads):
        sys.exit(f"missing cassandra-stress results in {RAW}")
    result = {
        "system": "scylladb",
        "tool": f"cassandra-stress {meta.get('stress_version', '?')} (scylladb/cassandra-stress:{os.environ.get('STRESS_VERSION', '?')})",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {"server": meta.get("server_version"), "cassandra_stress": meta.get("stress_version")},
        "cluster": {
            "nodes": int(meta.get("nodes_up", 0)),
            "node_args": meta.get("node_args"),
            "replication_factor": int(meta["replication_factor"]),
            "consistency": meta["consistency"],
        },
        "parameters": {
            "duration_s": int(meta["duration_s"]),
            "threads": int(meta["threads"]),
            "keys": int(meta["keys"]),
            "read_range": int(meta.get("read_range", 0)),
            "lwt_keys": int(meta["lwt_keys"]),
        },
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "workloads": workloads,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    c, p, m = result["cluster"], result["parameters"], result["machine"]
    print(
        f"\nScyllaDB {result['versions']['server']}, {c['nodes']} nodes ({c['node_args']}), "
        f"RF={c['replication_factor']}, CL={c['consistency']} | {result['tool']} | {p['threads']} threads, "
        f"{p['duration_s']} s per workload, reads over {p['read_range']:,} partitions"
    )
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})")
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {c['cpus']} CPUs / {c['memory_mib']} MiB" for n, c in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs\n")
    else:
        print()
    hdr = f"{'workload':<13} {'op':<6} {'ops/s':>9} {'mean ms':>8} {'p50 ms':>7} {'p95 ms':>7} {'p99 ms':>7} {'p99.9 ms':>9} {'errors':>7}"
    print(hdr)
    print("-" * len(hdr))
    for w in workloads:
        for op, o in w["ops"].items():
            print(
                f"{w['name']:<13} {op:<6} {o.get('ops_per_s', 0):>9,} {o.get('mean_ms', 0):>8.2f} "
                f"{o.get('p50_ms', 0):>7.2f} {o.get('p95_ms', 0):>7.2f} {o.get('p99_ms', 0):>7.2f} "
                f"{o.get('p99.9_ms', 0):>9.2f} {o.get('errors', 0):>7}"
            )
    print(f"\nraw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
