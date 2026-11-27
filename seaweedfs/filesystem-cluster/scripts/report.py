"""Curate a bounded recovery trial; keep raw logs and unsuccessful trials local."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import control as c
from production import rows


def operation_summary(path, success_event):
    data = rows(path)
    successes = [r for r in data if r.get("event") == success_event]
    stamps = [r.get("utc_end_ms", r["utc_ms"]) for r in successes]
    timings = [r["duration_ms"] for r in successes]
    return {
        "successes": len(successes),
        "ambiguous": sum(r.get("event") == "ambiguous" for r in data),
        "read_rpc_errors": sum(r.get("event") == "read_cycle_fail" for r in data),
        "max_success_operation_ms": max(timings, default=None),
        "max_success_completion_gap_ms": max(
            (b - a for a, b in zip(stamps, stamps[1:])), default=None
        ),
        "application_resources": next(
            (r for r in data if r.get("event") == "resources"), None
        ),
    }


def native_cgroup(row):
    sections = {}
    name = None
    for line in row["counters"].splitlines():
        if line.startswith("METRIC:"):
            name = line.removeprefix("METRIC:")
            sections[name] = []
        elif name:
            sections[name].append(line)
    cpu = dict(line.split() for line in sections.get("cpu.stat", []))
    io = {}
    for line in sections.get("io.stat", []):
        for field in line.split()[1:]:
            key, value = field.split("=")
            io[key] = io.get(key, 0) + int(value)
    peak = sections.get("memory.peak", [])
    cache = sections.get("cache_bytes", [])
    return {
        "lifetime_cpu_core_s": int(cpu["usage_usec"]) / 1e6 if cpu else None,
        "memory_peak_bytes": int(peak[0]) if peak else None,
        "lifetime_block_io": io or None,
        "cache_apparent_bytes": int(cache[0].split()[0]) if cache else None,
        "collector_error": row["measurement_error"],
    }


def main():
    case, resources, native, locks = [
        Path(p).resolve().relative_to(c.ROOT) for p in sys.argv[1:5]
    ]
    seed = next(
        r["run"] for r in rows(case / "reader.log") if r.get("event") == "verified"
    )
    candidates = [
        c.ROOT / "results/fixture.json",
        *(c.ROOT / "results").glob("retired-fixture-*.json"),
    ]
    fixtures = [
        json.loads(p.read_text())
        for p in candidates
        if p.exists() and json.loads(p.read_text())["runs"][0] == seed
    ]
    if len(fixtures) != 1:
        raise RuntimeError("No unique original fixture for the reported reader")
    metrics = json.loads(
        subprocess.check_output(
            [sys.executable, "scripts/resource_report.py", str(resources)], text=True
        )
    )
    storage = json.loads(native.read_text())
    report = {
        "classification": "Level-3 batch, initial correctness/recovery diagnostics; no comparative performance winner",
        "native_versions": (c.ROOT / "results/native-versions.log").read_text().strip(),
        "final_result": json.loads((case / "result.json").read_text()),
        "lock_probe": json.loads((locks / "result.json").read_text()),
        "writer": operation_summary(case / "writer.log", "ack"),
        "reader_cycles": operation_summary(case / "reader.log", "read_cycle"),
        "reader_files_per_successful_cycle": fixtures[0]["files"],
        "environment": metrics["environment"]["environment"],
        "sampled_stack_memory_peak_bytes": metrics[
            "sampled_aggregate_memory_peak_bytes"
        ],
        "roles": {},
        "owned_volumes": storage["volumes"],
        "total_owned_volume_allocated_bytes": sum(
            v["allocated_bytes"] for v in storage["volumes"].values()
        ),
        "native_lifetime_cgroups": {
            role: native_cgroup(row) for role, row in storage["native_cgroups"].items()
        },
        "compression": {
            "measured_codec_ratio": None,
            "reason": "Deterministic SHAKE256 body corpus; no paired compressed/uncompressed trial or isolated compression CPU measurement. Volume allocation includes replication and metadata; it is not a compression ratio.",
        },
        "historical_failed_trials": [],
        "teardown": (
            json.loads(
                (
                    c.ROOT / "results" / ("destroyed-" + fixtures[0]["id"] + ".json")
                ).read_text()
            )
            if (
                c.ROOT / "results" / ("destroyed-" + fixtures[0]["id"] + ".json")
            ).exists()
            else {"completed": False}
        ),
        "limits": metrics["limits"]
        + [
            "Final resource window includes verification, locks and outage, not just server steady state.",
            "Historical reads use the seed; newly acknowledged files are checked after recovery through both mounts and all native replicas.",
            "Both filer gateways were killed; etcd quorum and all three volume servers remained healthy. No disk/quorum/host failure qualification.",
            "Native cgroup I/O counters are container-lifetime totals. Disk sizes are a later non-atomic snapshot; before-load/peak disk sizing and device latency remain gaps.",
            "All zones/containers share one Docker VM and host; neither backups nor independent failure domains are established.",
            "Application resource records cover that C++ process; mount-daemon cost is included separately in its container role.",
        ],
        "raw_evidence_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                case / "result.json",
                case / "writer.log",
                case / "reader.log",
                case / "killed.json",
                resources,
                native,
                locks / "holder.log",
                locks / "contender.log",
            ]
        },
        "application_source_sha256": hashlib.sha256(
            (c.ROOT / "files-workload/main.cpp").read_bytes()
        ).hexdigest(),
    }
    for actor in metrics["actors"]:
        report["roles"][
            actor["name"].removeprefix("/seaweedfs-filesystem-cluster-")
        ] = {
            k: actor[k]
            for k in [
                "configuration",
                "counter_deltas",
                "peak_memory_bytes",
                "sampled_mean_memory_bytes",
                "observed_s",
                "cpu_utilization_pct_one_core_average",
                "cpu_utilization_pct_one_core_min",
                "cpu_utilization_pct_one_core_max",
            ]
            if k in actor
        }
    for path in sorted((c.ROOT / "results").glob("*/status.json")):
        if not json.loads(path.read_text())["passed"]:
            report["historical_failed_trials"].append(
                {
                    "case": str(path.parent.relative_to(c.ROOT)),
                    "status_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    target = c.ROOT / "reports/QUALIFICATION.json"
    c.save(target, report)
    print(target)


if __name__ == "__main__":
    main()
