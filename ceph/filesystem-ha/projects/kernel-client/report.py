"""Curate completed paired trials without changing their original evidence."""

import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from test import events


def disk_totals(snapshot):
    result = dict(read_bytes=0, write_bytes=0, reads=0, writes=0)
    for guest in snapshot["guests"].values():
        for line in guest["diskstats"].splitlines():
            fields = line.split()
            # Whole virtual SCSI OSD disks only; no dm/LVM/partition double count.
            if fields[0] == "8" and fields[2] in ("sda", "sdb", "sdc"):
                for key, index, scale in (
                    ("reads", 3, 1),
                    ("read_bytes", 5, 512),
                    ("writes", 7, 1),
                    ("write_bytes", 9, 512),
                ):
                    result[key] += int(fields[index]) * scale
    return result


def main():
    case, before_path, after_path, observer, mounts, remount = (
        Path(value).resolve() for value in sys.argv[1:]
    )
    result = json.loads((case / "result.json").read_text())
    if len(result.get("trials", [])) != 6 or not result.get("aggregate"):
        raise RuntimeError("Comparison has not passed its final verification")
    before, after = [json.loads(path.read_text()) for path in (before_path, after_path)]
    sampling = json.loads(
        subprocess.check_output(
            [sys.executable, str(ROOT / "scripts/resource_report.py"), str(observer)],
            text=True,
        )
    )
    result["classification"] = (
        "Level-3 complete access-path diagnostics; recent writes, no explicit cache eviction"
    )
    result["limits"] = [
        text
        for text in result["limits"]
        if not text.startswith("All data and host caches")
    ]
    result["limits"] += [
        "Physical cache residency is unverified; this is not a physical cold-cache comparison.",
        "SDK cross-directory rename (/staging to /objects) differs from kernel same-directory rename. Results do not isolate client-layer overhead.",
        "Native guest I/O deltas include all verification and background work in the comparison window; they cannot be attributed to individual paths.",
        "No compaction/codec comparison: compression disabled; high-entropy data; storage ratios include replication, metadata and allocation.",
    ]
    for backend in ("libcephfs", "kernel"):
        for operation in ("write", "read"):
            values = [
                row[operation]["operations_per_s"]
                for row in result["trials"]
                if row["backend"] == backend
            ]
            quartiles = statistics.quantiles(values, n=4, method="inclusive")
            result["aggregate"][backend][operation + "_operations_per_s"]["iqr"] = (
                quartiles[2] - quartiles[0]
            )
    process_usage = []
    for repeat in range(1, 4):
        cell = case / ("repeat-" + str(repeat))
        logs = [
            next(cell.glob("seed-*.log")),
            next(cell.glob("fresh-verify-*.log")),
            cell / "kernel-seed.log",
            cell / "guest-2.log",
        ]
        for path in logs:
            rows = events(path.read_text())
            usage = [
                row
                for row in rows
                if row.get("event") in ("resources", "process_resources")
            ]
            for row in usage:
                row.pop("proc_status", None)
            process_usage.append(
                {"evidence": str(path.relative_to(ROOT)), "resources": usage}
            )
    first, last = disk_totals(before), disk_totals(after)
    result["resources"] = {
        "docker": sampling,
        "processes": process_usage,
        "guest_osd_whole_disk_deltas": {key: last[key] - first[key] for key in first},
        "ceph_df_after": after["df"],
        "sparse_disks_after": after["virtual_disks"],
        "native_snapshot_before": str(before_path),
        "native_snapshot_after": str(after_path),
    }
    result["provenance"] = {
        "comparison": str(case),
        "raw_result_sha256": hashlib.sha256(
            (case / "result.json").read_bytes()
        ).hexdigest(),
        "mounts": str(mounts.relative_to(ROOT)),
        "remount": str(remount.relative_to(ROOT)),
    }
    fixtures = [
        json.loads(path.read_text())
        for path in (ROOT / "results").glob("retired-fixture-*.json")
    ]
    for fixture in fixtures:
        receipt = ROOT / "results" / ("destroyed-" + fixture["id"] + ".json")
        if fixture["fsid"] == after["status"]["fsid"] and receipt.exists():
            result["cleanup"] = json.loads(receipt.read_text())
    target = Path(__file__).parent / "reports/COMPARISON-2026-11-27.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(target)


if __name__ == "__main__":
    main()
