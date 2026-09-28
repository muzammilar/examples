"""`make benchmark-ycql`, part 2: parse /results/$NAME.txt (yb-sample-apps output from
bench/run-ycql.sh), print a summary table and write /results/$NAME.json. Standard library only.

Per run and operation: ops/s over the steady part (from the second 5 s status line to the
last, so connection setup and the tool's initial count(*) are left out), the mean latency over
the same intervals, and p99/max over every operation from the tool's cumulative JSON metrics
(it computes no other percentiles)."""

import json
import os
import platform
import re
import sys
from datetime import datetime, timezone

NAME = os.environ["NAME"]
RAW = f"/results/{NAME}.txt"

OP = r"([\d.]+) ops/sec \(([\d.]+) ms/op\), (\d+) total ops"
STATUS = re.compile(rf"Read: {OP}\s*\|\s*Write: {OP}\s*\|\s*Uptime: (\d+) ms")
JSON = re.compile(r"<json>(.*)</json>")


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
                name, _, threads = head.partition(" threads=")
                cur = {"workload": name, "threads": int(threads), "command": cmd, "status": [], "json": None,
                       "exceptions": 0, "fatal": 0}
                runs.append(cur)
            elif cur is None:
                continue
            elif m := STATUS.search(line):
                g = m.groups()
                cur["status"].append({
                    "read": (float(g[0]), float(g[1]), int(g[2])),
                    "write": (float(g[3]), float(g[4]), int(g[5])),
                    "uptime_ms": int(g[6]),
                })
            elif m := JSON.search(line):
                cur["json"] = json.loads(m[1])
            elif "Caught Exception" in line:
                cur["exceptions"] += 1
            elif " FATAL " in line:
                cur["fatal"] += 1
    return meta, [summarize(r) for r in runs]


def summarize(run):
    st, js = run.pop("status"), run.pop("json") or {}
    # skip the first interval (connection setup, count(*)) when there are enough of them; a short
    # run (the load in a smoke test) is measured from the workload start instead
    zero = {"read": (0.0, 0.0, 0), "write": (0.0, 0.0, 0), "uptime_ms": 0}
    first = 1 if len(st) >= 3 else 0
    if first == 0:
        st = [zero] + st
    ops = {}
    for op, key in (("read", "Read"), ("write", "Write")):
        if len(st) < 2 or st[-1][op][2] == 0:
            continue
        a, b = st[first], st[-1]
        secs = (b["uptime_ms"] - a["uptime_ms"]) / 1000
        n = b[op][2] - a[op][2]
        # interval mean latencies, weighted by the ops in each interval
        w = [(s[op][2] - p[op][2], s[op][1]) for p, s in zip(st[first:], st[first + 1 :])]
        cnt = sum(c for c, _ in w)
        lat = js.get(key, {}).get("latency", {})
        ops[op] = {
            "ops_per_s": round(n / secs) if secs > 0 else 0,
            "steady_seconds": round(secs, 1),
            "total_ops": b[op][2],
            "mean_ms": round(sum(c * ms for c, ms in w) / cnt, 3) if cnt else None,
            "p99_ms": lat.get("p99"),
            "max_ms": lat.get("max"),
            "mean_ms_all": lat.get("mean"),
            "latency_samples": int(lat.get("sampleSize", 0)),
        }
    run["ops"] = ops
    return run


def main():
    meta, runs = parse(RAW)
    if not runs or not all(r["ops"] for r in runs):
        sys.exit(f"missing yb-sample-apps results in {RAW}")
    result = {
        "system": "yugabytedb",
        "api": "ycql",
        "tool": f"yb-sample-apps {meta.get('sample_apps_version', '?')} CassandraKeyValue ({meta.get('java_version', 'java ?')})",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {"server": (re.search(r"-YB-([\w.-]+)", meta.get("server_version", "")) or [None, "?"])[1], "yb_sample_apps": meta.get("sample_apps_version")},
        "cluster": {
            "nodes": meta.get("nodes", "").split(","),
            "tservers_alive": int(meta.get("tservers_alive", 0)),
            "replication_factor": int(meta.get("replication_factor", 0)),
            "consistency": "YCQL default (QUORUM: strongly consistent reads from the tablet leader)",
        },
        "parameters": {
            "duration_s": int(meta["duration_s"]),
            "threads": [int(t) for t in meta["threads"].split()],
            "keys": int(meta["keys"]),
            "value_size": int(meta["value_size"]),
            "load_threads": int(meta["load_threads"]),
        },
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "runs": runs,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    c, p, m = result["cluster"], result["parameters"], result["machine"]
    print(
        f"\nYugabyteDB {result['versions']['server']} YCQL | {len(c['nodes'])} nodes, "
        f"{c['tservers_alive']} tservers alive, RF={c['replication_factor']} | {result['tool']}"
    )
    print(
        f"{p['keys']:,} keys x {p['value_size']} B, {p['duration_s']} s per timed run | "
        f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})"
    )
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {c['cpus']} CPUs / {c['memory_mib']} MiB" for n, c in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs")
    print()
    hdr = f"{'workload':<8} {'threads':>7} {'op':<6} {'ops/s':>9} {'mean ms':>8} {'p99 ms':>8} {'max ms':>8} {'errors':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in runs:
        for op, o in r["ops"].items():
            print(
                f"{r['workload']:<8} {r['threads']:>7} {op:<6} {o['ops_per_s']:>9,} {o['mean_ms'] or 0:>8.2f} "
                f"{o['p99_ms'] or 0:>8.2f} {o['max_ms'] or 0:>8.1f} {r['exceptions'] + r['fatal']:>7}"
            )
    print(f"\nraw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
