"""Summarize immutable paired/fault evidence with explicit metric scopes."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

import http_project as http
from production import rows
from report import native_cgroup


def main():
    comparison, fault, observer, before, after = [
        Path(value).resolve() for value in sys.argv[1:]
    ]
    result = json.loads((comparison / "result.json").read_text())
    if (
        len(result.get("trials", [])) != 6
        or not json.loads((fault / "status.json").read_text())["passed"]
    ):
        raise RuntimeError("Comparison and failure/recovery must pass before reporting")
    result["classification"] = (
        "Level-3 bounded access-path diagnostics on recent writes; physical cache residency unverified"
    )
    result["fault"] = json.loads((fault / "result.json").read_text())
    result["fault"]["actors"] = {}
    for role in ("writer", "reader"):
        data = rows(fault / (role + ".log"))
        result["fault"]["actors"][role] = {
            "events": dict(Counter(row["event"] for row in data)),
            "errors": dict(Counter(row["error"] for row in data if "error" in row)),
            "max_operation_ms": max(
                (row["duration_ms"] for row in data if "duration_ms" in row),
                default=None,
            ),
        }
    result["process_resources"] = []
    for cell in sorted(comparison.glob("repeat-*")):
        for path in sorted(cell.glob("*.log")):
            usage = [row for row in rows(path) if row.get("event") == "resources"]
            result["process_resources"].append(
                {"evidence": str(path.relative_to(http.ROOT)), "resources": usage}
            )
    for backend in ("fuse", "http"):
        for operation in ("write", "read"):
            values = [
                row[operation]["operations_per_s"]
                for row in result["trials"]
                if row["backend"] == backend
            ]
            q = statistics.quantiles(values, n=4, method="inclusive")
            result["aggregate"][backend][operation + "_operations_per_s"]["iqr"] = (
                q[2] - q[0]
            )
    result["docker_resources"] = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                str(http.ROOT / "scripts/resource_report.py"),
                str(observer),
            ],
            text=True,
        )
    )
    first, last = [json.loads(path.read_text()) for path in (before, after)]
    result["storage_after"] = last["volumes"]
    result["native_cgroups"] = {}
    for role, counters in last["native_cgroups"].items():
        old = native_cgroup(first["native_cgroups"][role])
        new = native_cgroup(counters)
        old_io, new_io = old["lifetime_block_io"], new["lifetime_block_io"]
        result["native_cgroups"][role] = {
            "memory_peak_bytes": new["memory_peak_bytes"],
            "cache_apparent_bytes": new["cache_apparent_bytes"],
            "cpu_core_s_delta": (
                new["lifetime_cpu_core_s"] - old["lifetime_cpu_core_s"]
                if new["lifetime_cpu_core_s"] is not None
                and old["lifetime_cpu_core_s"] is not None
                else None
            ),
            "block_io_delta": (
                {key: value - old_io.get(key, 0) for key, value in new_io.items()}
                if old_io is not None and new_io is not None
                else None
            ),
            "collector_error": new["collector_error"],
        }
    result["limits"] += [
        "Native cgroup deltas include all trials, exact readback and background work in the snapshot window. They are not per-path costs.",
        "Compression/codec comparison unrun: high-entropy bodies, no enabled workload codec; allocated/logical ratios include RF3, metadata, volume and cache allocation.",
        "Timed trials verify exact bodies after each write phase and at the end; no concurrent periodic readback runs inside the timed write loops. The live writer/reader fault test is separate.",
        "No physical power-loss or independent-host durability claim. Process loss is distinct from permanent disk or metadata-quorum loss.",
    ]
    result["provenance"] = {
        "comparison": str(comparison.relative_to(http.ROOT)),
        "fault": str(fault.relative_to(http.ROOT)),
        "observer": str(observer.relative_to(http.ROOT)),
        "raw_comparison_sha256": hashlib.sha256(
            (comparison / "result.json").read_bytes()
        ).hexdigest(),
    }
    trial_runs = {row["run"] for row in result["trials"]}
    for path in (http.ROOT / "results/native-http").glob("retired-fixture-*.json"):
        fixture = json.loads(path.read_text())
        receipt = (
            http.ROOT / "results" / ("destroyed-" + fixture["parent_fixture"] + ".json")
        )
        if trial_runs.issubset(fixture["runs"]) and receipt.exists():
            result["cleanup"] = json.loads(receipt.read_text())
    target = Path(__file__).parent / "reports/COMPARISON-2026-11-27.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(target)


if __name__ == "__main__":
    main()
