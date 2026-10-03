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


def num(s):
    return float(s.replace(",", ""))


def parse(path):
    """sky-bench prints a header (`Skytable (skyd) : v0.8.4`, `Mode : threads=..`) and then one
    block per task:
        1. INSERT
        Throughput (full) : 257,243.5500 queries/sec
        Queries executed  : 100,000
        Full latency / mean (ms): 0.06 / max (ms): 4.7
        Latency distribution / Full  : 99% <= 0.222 ms, ..., 50% <= 0.052 ms"""
    meta, header, tasks = {}, {}, []
    task, section = None, None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("=== command: "):
                header["command"] = line[len("=== command: ") :]
            elif m := re.fullmatch(r"(Skytable \(skyd\)|sky-bench|Workload|Mode)\s*: (.*)", line):
                header[m[1]] = m[2].strip()
            elif m := re.fullmatch(r"\d+\. (\w+)\s*", line):
                task = {"task": m[1]}
                tasks.append(task)
            elif task is None:
                continue
            elif m := re.fullmatch(r"Throughput \((raw|full)\)\s*: ([\d,.]+) queries/sec", line):
                task[f"qps_{m[1]}"] = round(num(m[2]))
            elif m := re.fullmatch(r"Queries executed\s*: ([\d,]+)", line):
                task["queries"] = int(num(m[1]))
            elif line in ("Server latency", "Full latency"):
                section = line.split()[0].lower()
            elif section == "full" and (m := re.fullmatch(r"\s+(mean|max)\s+\(ms\): ([\d.]+)", line)):
                task[f"{m[1]}_ms"] = float(m[2])
            elif m := re.fullmatch(r"\s+Full\s*: (.*)", line):
                for p, v in re.findall(r"(\d+)% <= ([\d.]+) ms", m[1]):
                    task[f"p{p}_ms"] = float(v)
    return meta, header, tasks


def main():
    meta, header, tasks = parse(RAW)
    if not tasks or any("qps_full" not in t for t in tasks):
        sys.exit(f"no complete benchmark result in {RAW}")
    result = {
        "system": "skytable",
        "tool": f"sky-bench {header.get('sky-bench', '?')}",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {"skyd": header.get("Skytable (skyd)"), "sky_bench": header.get("sky-bench")},
        "workload": header.get("Workload"),
        "mode": header.get("Mode"),
        "parameters": {k: int(meta[k]) for k in ("rows", "threads", "connections")},
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "command": header.get("command"),
        "tasks": tasks,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    p, m = result["parameters"], result["machine"]
    print(
        f"\nSkytable {result['versions']['skyd']}, single node | sky-bench {result['workload']} | "
        f"{p['rows']:,} rows, {p['connections']} connections on {p['threads']} threads"
    )
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})")
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {c['cpus']} CPUs / {c['memory_mib']} MiB" for n, c in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs\n")
    else:
        print()
    hdr = f"{'task':<8} {'queries':>10} {'queries/s':>11} {'mean ms':>8} {'p50 ms':>7} {'p95 ms':>7} {'p99 ms':>7} {'max ms':>7}"
    print(hdr)
    print("-" * len(hdr))
    for t in tasks:
        print(
            f"{t['task']:<8} {t.get('queries', 0):>10,} {t['qps_full']:>11,} {t.get('mean_ms', 0):>8.3f} "
            f"{t.get('p50_ms', 0):>7.3f} {t.get('p95_ms', 0):>7.3f} {t.get('p99_ms', 0):>7.3f} {t.get('max_ms', 0):>7.2f}"
        )
    print(f"\nraw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
