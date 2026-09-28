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

# " NATS Core NATS subscriber aggregated stats: 3,659,079 msgs/sec ~ 447 MiB/sec"
# "NATS JetStream asynchronous publisher stats: 311,123 msgs/sec ~ 38 MiB/sec ~ min: 1,072.91us ~ ... ~ P99: 3,081.54us"
STATS = re.compile(
    r"NATS (?P<role>.+?) (?:aggregated )?stats: (?P<rate>[\d,]+) msgs/sec ~ (?P<bw>[\d,.]+) (?P<unit>[KMG]?i?B)/sec(?P<rest>.*)"
)
# with several publishers the latencies follow on their own line:
# " latencies per operation min 0us | avg 0.52us | ... | P50 0.04us | P90 0.08us | P99 0.12us | P99.9: 10.87us"
LAT = re.compile(r"(min|avg|max|P50|P90|P99|P99\.9):? ([\d,.]+)(us|ms|s)\b")
UNIT_MIB = {"B": 1 / 2**20, "KiB": 1 / 2**10, "MiB": 1, "GiB": 2**10}
TO_MS = {"us": 1e-3, "ms": 1, "s": 1e3}
ROLES = {  # nats bench's label -> short name, in table order
    "Core NATS publisher": "pub",
    "Core NATS subscriber": "sub",
    "Core NATS service requester": "request",
    "JetStream synchronous publisher": "js pub sync",
    "JetStream asynchronous publisher": "js pub async",
    "JetStream durable consumer (fetch)": "js fetch",
}


def num(s):
    return float(s.replace(",", ""))


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


def latencies(text):
    return {f"{k.lower()}_ms": round(num(v) * TO_MS[u], 4) for k, v, u in LAT.findall(text)}


def parse(path):
    meta, workloads, cur, last = {}, [], None, None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("=== workload "):
                name, _, cmd = line[len("=== workload ") :].partition(": ")
                cur = {"name": name, "command": cmd, "results": []}
                workloads.append(cur)
            elif cur is not None and (m := STATS.search(line)):
                last = {
                    "role": ROLES.get(m["role"], m["role"]),
                    "msgs_per_s": int(num(m["rate"])),
                    "mib_per_s": round(num(m["bw"]) * UNIT_MIB.get(m["unit"], 1), 1),
                    **latencies(m["rest"]),
                }
                cur["results"].append(last)
            elif last is not None and line.startswith("latencies per operation"):
                last.update(latencies(line))
    order = list(ROLES.values())  # the subscriber may finish before or after the publisher
    for w in workloads:
        w["results"].sort(key=lambda r: order.index(r["role"]) if r["role"] in order else len(order))
    return meta, workloads


def main():
    meta, workloads = parse(RAW)
    if not workloads:
        sys.exit(f"no workloads in {RAW}")
    result = {
        "system": "nats",
        "tool": f"nats bench (nats CLI {meta.get('nats_cli_version', '?')}, natsio/nats-box:{os.environ.get('NATS_BOX_VERSION', '?')})",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {"server": meta.get("server_version"), "nats_cli": meta.get("nats_cli_version")},
        "server": {"nodes": 1, "jetstream_storage": "file", "stream_replicas": 1},
        "parameters": {k: int(meta[k]) for k in ("msgs", "clients", "req_msgs", "js_msgs", "js_sync_msgs")},
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "workloads": workloads,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    p, m = result["parameters"], result["machine"]
    print(
        f"\nNATS {result['versions']['server']}, 1 server, JetStream file storage R1 | {result['tool']} | "
        f"core {p['msgs']:,} msgs per run, request/reply {p['req_msgs']:,}, "
        f"JetStream {p['js_sync_msgs']:,} sync / {p['js_msgs']:,} async + fetch"
    )
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})")
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {c['cpus']} CPUs / {c['memory_mib']} MiB" for n, c in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs")
    print()
    hdr = f"{'workload':<20} {'side':<13} {'msgs/s':>11} {'MiB/s':>8} {'p50 ms':>8} {'p99 ms':>8}"
    print(hdr)
    print("-" * len(hdr))
    for w in workloads:
        for r in w["results"]:
            # core publish "latency" is the time to buffer one message client-side: not shown
            core = r["role"] in ("pub", "sub")
            p50 = "-" if core or "p50_ms" not in r else f"{r['p50_ms']:.3f}"
            p99 = "-" if core or "p99_ms" not in r else f"{r['p99_ms']:.3f}"
            print(f"{w['name']:<20} {r['role']:<13} {r['msgs_per_s']:>11,} {r['mib_per_s']:>8,.1f} {p50:>8} {p99:>8}")
    print("\nsub = all subscribers together (each receives every message); js pub async latency is per batch of 500")
    print(f"raw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
