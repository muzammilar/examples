"""`make benchmark`, part 2: parse /results/$NAME.txt (written by bench/run.sh),
print a summary table and write /results/$NAME.json. Standard library only."""

import json
import os
import platform
import re
import sys
from datetime import datetime, timezone

NAME = os.environ["NAME"]
RAW = f"/results/{NAME}.txt"
PCTS = ["p50", "p95", "p99", "p99.9"]  # the --percentiles in run.sh, in that order

INFO = re.compile(r"(\w+)\(tps=\d+ \(hit=\d+ miss=\d+\) timeouts=(\d+) errors=(\d+)\)")


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
    meta, workloads, cur, hist_op = {}, [], None, None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("=== workload "):
                name, _, cmd = line[len("=== workload ") :].partition(": ")
                cur = {"name": name, "command": cmd, "ops": {}}
                workloads.append(cur)
            elif cur is None:
                continue
            elif line.startswith("hdr: "):
                # hdr: <op> <utc> <secs>, <total>, <min>, <max>, <p50>, <p95>, <p99>, <p99.9>  (µs)
                parts = line[5:].split()
                op, nums = parts[0], [float(x) for x in "".join(parts[2:]).split(",")]
                o = cur["ops"].setdefault(op, {"timeouts": 0, "errors": 0})
                o["count"] = int(nums[1])
                o["min_ms"], o["max_ms"] = nums[2] / 1000, nums[3] / 1000
                for p, v in zip(PCTS, nums[4:]):
                    o[f"{p}_ms"] = v / 1000
            elif " INFO " in line and "(tps=" in line:
                for op, timeouts, errors in INFO.findall(line):
                    if op != "total":
                        o = cur["ops"].setdefault(op, {"timeouts": 0, "errors": 0})
                        o["timeouts"] += int(timeouts)
                        o["errors"] += int(errors)
            elif line.startswith("cumulative: "):
                m = re.match(r"cumulative: op=(\w+) interval=([\d.]+),([\d.]+)", line)
                if m:
                    o = cur["ops"].setdefault(m[1], {})
                    o["seconds"] = round(float(m[3]) - float(m[2]), 3)
            elif line.startswith("--- histogram op="):
                hist_op = line.split("=", 1)[1].split()[0]
            elif line.startswith("#[Mean") and hist_op in cur["ops"]:
                m = re.search(r"Mean\s*=\s*([\d.]+)", line)
                if m:
                    cur["ops"][hist_op]["mean_ms"] = float(m[1]) / 1000
    for w in workloads:
        for o in w["ops"].values():
            if o.get("seconds"):
                o["ops_per_s"] = round(o.get("count", 0) / o["seconds"])
        w["total_ops_per_s"] = sum(o.get("ops_per_s", 0) for o in w["ops"].values())
    return meta, workloads


def main():
    meta, workloads = parse(RAW)
    if not workloads:
        sys.exit(f"no workloads in {RAW}")
    result = {
        "system": "aerospike",
        "tool": f"asbench {meta.get('asbench_version', '?')} (aerospike/aerospike-tools:{os.environ.get('TOOLS_VERSION', '?')})",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {"server": meta.get("server_version"), "asbench": meta.get("asbench_version")},
        "cluster": {
            "nodes": int(meta.get("cluster_size", 0)),
            "namespace": "test",
            "replication_factor": int(meta.get("replication_factor", 0)),
            "storage_engine": meta.get("storage"),
        },
        "parameters": {
            "duration_s": int(meta["duration_s"]),
            "threads": int(meta["threads"]),
            "keys": int(meta["keys"]),
            "object_spec": meta["object_spec"],
        },
        "machine": machine(),
        "workloads": workloads,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    c, p = result["cluster"], result["parameters"]
    print(
        f"\nAerospike {result['versions']['server']}, {c['nodes']} nodes, RF={c['replication_factor']}, "
        f"{c['storage_engine']} | {result['tool']} | {p['threads']} threads, {p['keys']:,} keys "
        f"({p['object_spec']}), {p['duration_s']} s per timed workload"
    )
    m = result["machine"]
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})\n")
    hdr = f"{'workload':<12} {'op':<6} {'ops/s':>10} {'mean ms':>8} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'p99.9 ms':>9} {'errors':>7}"
    print(hdr)
    print("-" * len(hdr))
    for w in workloads:
        for op, o in w["ops"].items():
            print(
                f"{w['name']:<12} {op:<6} {o.get('ops_per_s', 0):>10,} {o.get('mean_ms', 0):>8.3f} "
                f"{o.get('p50_ms', 0):>8.3f} {o.get('p95_ms', 0):>8.3f} {o.get('p99_ms', 0):>8.3f} "
                f"{o.get('p99.9_ms', 0):>9.3f} {o.get('errors', 0) + o.get('timeouts', 0):>7}"
            )
    print(f"\nraw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
