"""Curate one kernel recovery case and measured virtual-disk request costs."""

import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

import control


def diskstats(guest):
    rows = {}
    for line in guest["diskstats"].splitlines():
        fields = line.split()
        if fields[0] == "8" and fields[2] in ("sda", "sdb", "sdc"):
            rows[fields[2]] = {
                name: int(fields[index]) * scale
                for name, index, scale in (
                    ("reads", 3, 1),
                    ("read_bytes", 5, 512),
                    ("read_ms", 6, 1),
                    ("writes", 7, 1),
                    ("write_bytes", 9, 512),
                    ("write_ms", 10, 1),
                    ("weighted_io_ms", 13, 1),
                )
            }
    if len(rows) != 3:
        raise RuntimeError("Expected three complete native OSD-disk counters per guest")
    return rows


def native_daemons(before, after, victim):
    def number(value):
        return int(value) if isinstance(value, str) and value.isdecimal() else None

    result = {}
    for node, guest in after["guests"].items():
        elapsed = (
            guest["sample_monotonic_ns"] - before["guests"][node]["sample_monotonic_ns"]
        ) / 1e9
        for name, unit in guest["units"].items():
            old = before["guests"][node]["units"].get(name)
            first = number(old.get("CPUUsageNSec")) if old else 0
            last = number(unit.get("CPUUsageNSec"))
            restarted = name == "ceph-mds@" + victim + ".service"
            delta = (
                (last - first) / 1e9
                if first is not None
                and last is not None
                and last >= first
                and not restarted
                else None
            )
            result[node + "/" + name] = {
                "unit_created_during_window": old is None,
                "known_restarted": restarted,
                "cpu_core_s_delta": delta,
                "cpu_pct_one_core_average": (
                    100 * delta / elapsed if delta is not None else None
                ),
                "memory_current_bytes": number(unit.get("MemoryCurrent")),
                "memory_peak_bytes": number(unit.get("MemoryPeak")),
                "scope": "Unit counters; restarted MDS CPU delta is unavailable, while whole-guest Docker counters include it",
            }
    return result


def main():
    case, before_path, after_path, observer, namespace_path = [
        Path(value).resolve() for value in sys.argv[1:]
    ]
    if (
        not json.loads((case / "status.json").read_text())["passed"]
        or json.loads((case / "finalizer.json").read_text())["errors"]
    ):
        raise RuntimeError("Recovery and finalizer must pass")
    before, after = [json.loads(path.read_text()) for path in (before_path, after_path)]
    if before["status"]["fsid"] != after["status"]["fsid"]:
        raise RuntimeError("Snapshots belong to different clusters")
    rows = control.native_checks.events((case / "writer.log").read_text())
    acks = [row for row in rows if row.get("event") == "ack"]
    if len(acks) != 512 or any(
        row.get("event") in ("ambiguous", "fatal") for row in rows
    ):
        raise RuntimeError("Do not qualify an incomplete/error-bearing kernel corpus")
    reader = control.native_checks.events((case / "reader.log").read_text())
    sampling = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                str(control.ROOT / "scripts/resource_report.py"),
                str(observer),
            ],
            text=True,
        )
    )
    disks = {}
    for node in before["guests"]:
        first, last = before["guests"][node], after["guests"][node]
        elapsed = (last["sample_monotonic_ns"] - first["sample_monotonic_ns"]) / 1e9
        if first["boot_id"] != last["boot_id"] or elapsed <= 0:
            raise RuntimeError(
                "Guest clock/counter identity changed during the MDS case"
            )
        old, new = diskstats(first), diskstats(last)
        for name in old:
            delta = {key: new[name][key] - value for key, value in old[name].items()}
            if any(value < 0 for value in delta.values()):
                raise RuntimeError("Virtual-disk counters reset")
            disks[node + "/" + name] = {
                "counter_deltas": delta,
                "observed_s": elapsed,
                "read_iops": delta["reads"] / elapsed,
                "write_iops": delta["writes"] / elapsed,
                "read_bytes_per_s": delta["read_bytes"] / elapsed,
                "write_bytes_per_s": delta["write_bytes"] / elapsed,
                "mean_read_completion_ms": (
                    delta["read_ms"] / delta["reads"] if delta["reads"] else None
                ),
                "mean_write_completion_ms": (
                    delta["write_ms"] / delta["writes"] if delta["writes"] else None
                ),
                "mean_outstanding_virtual_ios": delta["weighted_io_ms"]
                / (elapsed * 1000),
            }
    durations = sorted(row["duration_ms"] for row in acks)
    stamps = [row["utc_end_ms"] for row in acks]
    namespaces = []
    for path in sorted(namespace_path.rglob("guest-*-namespace.json")):
        row = json.loads(path.read_text())
        if not row["all_regular_files"]:
            raise RuntimeError("Supplemental namespace check found a non-file")
        namespaces.append(
            {
                "run": row["run"],
                "node": row["node"],
                "files": len(row["names"]),
                "evidence": str(path.relative_to(control.ROOT)),
            }
        )
    if len(namespaces) < 4:
        raise RuntimeError("Need both mounts for baseline and live namespaces")
    library_rows = [
        row
        for path in (case / "library-readback").glob("*.log")
        for row in control.native_checks.events(path.read_text())
        if "libcephfs_version" in row
    ]
    result = {
        "date": "2026-11-27",
        "classification": "One bounded level-3 kernel-client MDS recovery diagnostic",
        "fsid": before["status"]["fsid"],
        "libcephfs_versions": sorted(
            {row["libcephfs_version"] for row in library_rows}
        ),
        "guest_kernels": {
            node: guest["kernel"] for node, guest in after["guests"].items()
        },
        "namespace_verification": namespaces,
        "recovery": json.loads((case / "recovery.json").read_text()),
        "fault": json.loads((case / "fault.json").read_text()),
        "live_readback": json.loads((case / "live-readback/result.json").read_text()),
        "old_kernel_readback": json.loads(
            (case / "old-kernel-readback/result.json").read_text()
        ),
        "library_readback": json.loads(
            (case / "library-readback/result.json").read_text()
        ),
        "writer": {
            "acks": len(acks),
            "ambiguous": 0,
            "mean_publication_ms": statistics.mean(durations),
            "p50_ms": durations[255],
            "p95_ms": durations[486],
            "p99_ms": durations[506],
            "max_ms": durations[-1],
            "max_ack_completion_gap_ms": max(b - a for a, b in zip(stamps, stamps[1:])),
            "process_resources": next(
                row for row in rows if row.get("event") == "resources"
            ),
        },
        "reader": {
            "successful_cycles": sum(row.get("event") == "verified" for row in reader),
            "body_reads": sum(row.get("event") == "body_read" for row in reader),
            "errors": sum(
                row.get("event") in ("fatal", "read_cycle_fail") for row in reader
            ),
            "process_resources": next(
                row for row in reader if row.get("event") == "resources"
            ),
        },
        "docker_resources": sampling,
        "virtual_osd_disks": disks,
        "native_daemons": native_daemons(
            before, after, json.loads((case / "fault.json").read_text())["name"]
        ),
        "storage_after": {
            "ceph_df": after["df"],
            "osd_perf_snapshot": after["osd_perf"],
            "data_pool_config": after["data_pool_config"],
            "metadata_pool_config": after["metadata_pool_config"],
            "virtual_disks": after["virtual_disks"],
        },
        "provenance": {
            "case": str(case.relative_to(control.ROOT)),
            "before": str(before_path.relative_to(control.ROOT)),
            "after": str(after_path.relative_to(control.ROOT)),
            "observer": str(observer.relative_to(control.ROOT)),
            "executed_controller_sha256": json.loads(
                (case / "provenance.json").read_text()
            ),
            "posix_source_sha256": hashlib.sha256(
                (Path(__file__).parent / "posix-workload-source/main.cpp").read_bytes()
            ).hexdigest(),
        },
        "limits": [
            "Guest whole-device counters measure virtual OSD disks, not physical host storage. Completion times include the virtual block path; they are not physical-device service latency.",
            "Per-guest monotonic sample windows are separate and non-atomic; rates include verification and background work.",
            "CPU/memory of the POSIX process excludes kernel/mount/backend memory; Docker includes guest RAM/caches and control jobs.",
            "No long soak, physical cold-cache comparison, disk/node-loss or independent backup/restore claim.",
            "Native guest CPU and Docker host CPU are different, overlapping accounting domains and must not be summed. Docker outer-interface network counters do not qualify complete private-guest replication traffic.",
            "Native pool compression is disabled; zero compressed counters are not a codec-ratio benchmark.",
            "Linux diskstats request times are accumulated and truncated to millisecond counters; derived mean times have that resolution limit.",
        ],
    }
    path = Path(__file__).parent / "reports/KERNEL-MDS-RECOVERY-2026-11-27.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(path)


if __name__ == "__main__":
    main()
