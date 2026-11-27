"""Compact one executed fault and its explicit Docker/native evidence paths."""

import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import lab


def operations(path, role):
    rows = [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.startswith("{")
    ]
    rows = [r for r in rows if r.get("event") in ("ack", "ambiguous", "read")]
    phases = {}
    for phase in sorted({r["phase_start"] for r in rows}):
        selected = [r for r in rows if r["phase_start"] == phase]
        times = sorted(r["duration_ms"] for r in selected)
        phases[phase or "startup"] = {
            "operations": len(selected),
            "latency_ms_nearest_rank": {
                label: times[max(0, math.ceil(len(times) * q) - 1)]
                for label, q in [("p50", 0.5), ("p95", 0.95), ("p99", 0.99), ("max", 1)]
            },
        }
    successes = [r for r in rows if r.get("event") == "ack" or r.get("result") == "ok"]
    stamps = sorted(r["utc_end_ms"] for r in successes)
    return {
        "operations": len(rows),
        "successes": len(successes),
        "errors_or_ambiguous": len(rows) - len(successes),
        "max_success_completion_gap_ms": max(
            (b - a for a, b in zip(stamps, stamps[1:])), default=None
        ),
        "phases": phases,
        "native_unit": json.loads(
            (path.parent / (role + "-resources.json")).read_text()
        ),
    }


def main():
    case, resource, native = map(Path, sys.argv[1:4])
    metrics = json.loads(
        subprocess.check_output(
            [sys.executable, "scripts/resource_report.py", str(resource)], text=True
        )
    )
    storage = json.loads(native.read_text())
    pools = {p["name"]: p["stats"] for p in storage["df"]["pools"]}
    scsi = {
        "read_ios": 0,
        "write_ios": 0,
        "read_bytes": 0,
        "write_bytes": 0,
        "device_io_ms": 0,
    }
    for guest in storage["guests"].values():
        for line in guest["diskstats"].splitlines():
            values = line.split()
            if int(values[0]) != 8 or int(values[1]) % 16:
                continue
            for name, index, scale in [
                ("read_ios", 3, 1),
                ("write_ios", 7, 1),
                ("read_bytes", 5, 512),
                ("write_bytes", 9, 512),
                ("device_io_ms", 12, 1),
            ]:
                scsi[name] += int(values[index]) * scale
    totals = {}
    for actor in metrics["actors"]:
        for key, value in actor["counter_deltas"].items():
            # Tools share the guests' network namespace; don't count its bytes twice.
            if key.startswith("network_") and "guests-" not in actor["name"]:
                continue
            totals[key] = totals.get(key, 0) + value
    result = {
        "classification": "correctness/recovery diagnostic; no comparative performance qualification",
        "case": str(case),
        "result": json.loads((case / "result.json").read_text()),
        "writer": operations(case / "writer.log", "writer"),
        "reader": operations(case / "reader.log", "reader"),
        "environment": metrics["environment"]["environment"],
        "docker_observation": {
            "path": str(resource),
            "counter_deltas": totals,
            "sampled_aggregate_memory_peak_bytes": metrics[
                "sampled_aggregate_memory_peak_bytes"
            ],
            "guest_vm_actor": [a for a in metrics["actors"] if "guests-" in a["name"]],
        },
        "storage": {
            "guest_scsi_lifetime": scsi,
            "native_snapshot": str(native),
            "ceph_raw_used_bytes": storage["df"]["stats"]["total_used_raw_bytes"],
            "file_data": pools["labfs_data"],
            "metadata": pools["labfs_meta"],
            "qcow2_apparent_bytes": sum(
                p["apparent_bytes"] for p in storage["virtual_disks"]
            ),
            "qcow2_allocated_bytes": sum(
                p["allocated_bytes"] for p in storage["virtual_disks"]
            ),
            "codec": "none",
            "compression_ratio": None,
            "compression_note": "Both CephFS pool compression counters are reported; no compressed counterpart or compression CPU comparison was run.",
        },
        "limits": metrics["limits"]
        + [
            "Docker CPU includes QEMU TCG emulation and guests; native unit CPU is inside guest kernels. These scopes cannot be added.",
            "Native storage counters are cumulative at the identified snapshot, not fault-only deltas.",
            "Guest SCSI counters include all owned whole SCSI disks, including unused spares and provisioning; OS root disks and device-mapper duplicates are excluded. Per-daemon io.stat is unavailable because IOAccounting was not enabled for those native daemon units.",
            "Historical immutable files are read during the fault; new live ACKs are reconciled by fresh clients afterwards.",
            "Sampling misses very short controls, builder work and sub-second peaks; physical disk latency is unavailable.",
            "Native PG placement proves the declared three-host layout, not independent raw replica hashes or a completed deep scrub.",
            "Completion gaps include the intentional 100 ms think time and controller polling. Percentiles are diagnostic samples, not steady-state benchmark estimates.",
        ],
        "evidence_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                case / "result.json",
                case / "killed.json",
                case / "writer.log",
                case / "reader.log",
                resource,
                native,
            ]
        },
        "application_source_sha256": hashlib.sha256(
            (lab.ROOT / "files-workload/main.cpp").read_bytes()
        ).hexdigest(),
    }
    target = (
        lab.ROOT
        / "reports"
        / ("FAILOVER-" + case.name.removeprefix("mds-failover-") + ".json")
    )
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(target)


if __name__ == "__main__":
    main()
