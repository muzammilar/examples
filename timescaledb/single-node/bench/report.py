"""`make benchmark`, part 2: parse /results/$NAME.txt (timescaledb-parallel-copy and psql output
from bench/run.sh), print a summary table and write /results/$NAME.json. Standard library only."""

import json
import os
import platform
import re
import statistics
import sys
from datetime import datetime, timezone

NAME = os.environ["NAME"]
RAW = f"/results/{NAME}.txt"
TIME = re.compile(r"^Time: ([\d.]+) ms")


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
    meta, ingest, phases, store, timings = {}, [], {}, {}, {}
    section, cur, phase, query = None, None, None, None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("meta: "):
                k, _, v = line[6:].partition("=")
                meta[k] = v
            elif line.startswith("=== ingest mode="):
                mode, workers = (kv.split("=")[1] for kv in line.split()[2:4])
                cur = {"mode": mode, "workers": int(workers)}
                ingest.append(cur)
                section = "ingest"
            elif line.startswith("=== queries phase="):
                phase = line.split()[2].split("=")[1]
                section, query = "queries", None
            elif line.startswith("=== "):
                section = line[4:].strip()
                store.setdefault(section, {})
            elif section == "ingest":
                k, _, v = line.partition(": ")
                if k == "elapsed_s":
                    cur["elapsed_s"] = float(v)
                elif k == "count":
                    cur["rows"] = int(v)
                elif k == "size_bytes":
                    cur["size_bytes"] = int(v)
            elif section == "queries":
                if line.startswith("query: "):
                    query = line[7:]
                elif (m := TIME.match(line)) and query:
                    phases.setdefault(phase, {}).setdefault(query, []).append(float(m[1]))
                    query = None
            elif section in ("rowstore", "columnstore", "cagg", "convert"):
                if m := TIME.match(line):
                    timings.setdefault(section, []).append(float(m[1]))
                k, _, v = line.partition(": ")
                if k in ("chunks", "size_bytes", "cagg_rows"):
                    store[section][k] = int(v)
                elif k in ("detail", "stats"):
                    a, b = (int(x) for x in v.split("|"))
                    store[section].update(
                        {"table_bytes": a, "index_bytes": b} if k == "detail" else {"before_bytes": a, "after_bytes": b}
                    )
    for r in ingest:
        r["rows_per_s"] = round(r["rows"] / r["elapsed_s"]) if r.get("elapsed_s") else 0
    return meta, ingest, phases, store, timings


def main():
    meta, ingest, phases, store, timings = parse(RAW)
    if not ingest or not phases.get("rowstore") or not phases.get("columnstore"):
        sys.exit(f"missing ingest or query results in {RAW}")
    rs, cs = store["rowstore"], store["columnstore"]
    queries = {
        ph: [{"query": q, "runs_ms": t, "min_ms": min(t), "median_ms": statistics.median(t)} for q, t in qs.items()]
        for ph, qs in phases.items()
    }
    result = {
        "system": "timescaledb",
        "tool": f"timescaledb-parallel-copy {meta.get('parallel_copy_version', '?')} + psql",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": {"server": meta.get("server_version"), "timescaledb": meta.get("timescaledb_version")},
        "parameters": {
            "devices": int(meta["devices"]),
            "days": int(meta["days"]),
            "rows": int(meta["rows"]),
            "csv_mib": int(meta.get("csv_mib", 0)),
            "batch": int(meta["batch"]),
            "runs": int(meta["runs"]),
        },
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "ingest": ingest,
        "storage": {
            "chunks": rs.get("chunks"),
            "rowstore_bytes": rs.get("size_bytes"),
            "rowstore_table_bytes": rs.get("table_bytes"),
            "rowstore_index_bytes": rs.get("index_bytes"),
            "columnstore_bytes": cs.get("size_bytes"),
            "columnstore_stats_before_bytes": cs.get("before_bytes"),
            "columnstore_stats_after_bytes": cs.get("after_bytes"),
            "ratio_total": round(rs["size_bytes"] / cs["size_bytes"], 1),
            "convert_ms": sum(timings.get("convert", [])),
            "cagg_build_ms": sum(timings.get("cagg", [])),
            "cagg_rows": store.get("cagg", {}).get("cagg_rows"),
        },
        "queries": queries,
        "raw": os.path.basename(RAW),
    }
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(result, f, indent=2)

    p, m, s = result["parameters"], result["machine"], result["storage"]
    print(f"\n{result['versions']['server']}\nTimescaleDB {result['versions']['timescaledb']} | {result['tool']}")
    print(
        f"{p['rows']:,} rows = {p['devices']:,} devices x {p['days']} days x 1/min (CSV {p['csv_mib']} MiB), "
        f"batches of {p['batch']}, {p['runs']} runs per query"
    )
    print(f"Docker VM: {m['docker_vm_cpus']} CPUs, {m['docker_vm_memory_gib']} GiB ({m['arch']})")
    if lim := result["limits"]:
        caps = ", ".join(f"{n} {c['cpus']} CPUs / {c['memory_mib']} MiB" for n, c in lim["containers"].items())
        print(f"limits: {caps}; bench client {lim['bench_client_cpus']} CPUs")
    mib = 2**20
    hdr = f"\n{'ingest into':<12} {'workers':>7} {'rows':>12} {'seconds':>8} {'rows/s':>10} {'size MiB':>9}"
    print(hdr + "\n" + "-" * (len(hdr) - 1))
    for r in ingest:
        print(
            f"{r['mode']:<12} {r['workers']:>7} {r['rows']:>12,} {r['elapsed_s']:>8.1f} {r['rows_per_s']:>10,} "
            f"{r.get('size_bytes', 0) / mib:>9,.1f}"
        )
    print(
        f"\nstorage: {s['chunks']} chunks; rowstore {s['rowstore_bytes'] / mib:,.0f} MiB "
        f"(table {s['rowstore_table_bytes'] / mib:,.0f} + indexes {s['rowstore_index_bytes'] / mib:,.0f}); "
        f"columnstore {s['columnstore_bytes'] / mib:,.1f} MiB -> {s['ratio_total']}x smaller "
        f"(converted in {s['convert_ms'] / 1000:.1f} s)"
    )
    print(f"continuous aggregate: {s['cagg_rows']:,} hourly rows, built in {s['cagg_build_ms'] / 1000:.1f} s")
    names = [q["query"] for q in queries["rowstore"]]
    med = {ph: {q["query"]: q["median_ms"] for q in qs} for ph, qs in queries.items()}
    hdr = f"\n{'query (median ms)':<22} {'rowstore':>10} {'columnstore':>12} {'cont. agg':>10}"
    print(hdr + "\n" + "-" * (len(hdr) - 1))
    for n in names:
        cagg = med.get("cagg", {}).get(n)
        print(
            f"{n:<22} {med['rowstore'][n]:>10.1f} {med['columnstore'][n]:>12.1f} "
            f"{(f'{cagg:.1f}' if cagg is not None else '-'):>10}"
        )
    print(f"\nraw: results/{NAME}.txt  parsed: results/{NAME}.json")


if __name__ == "__main__":
    main()
