"""Summarize saved observations after cleanup; never query or repair storage."""

import json
import os
from pathlib import Path

from witness import records


def read(path):
    return json.loads(path.read_text())


def summarize(root):
    histories = read(root / "fault-history.json")
    required = {v["object"]["key"] for v in histories if v["acked"]}
    for name in ["manifest", "cases-manifest", "benchmark-manifest"]:
        path = root / (name + ".json")
        if path.exists():
            required.update(v["key"] for v in read(path))
    reconciliation = read(root / "reconciliation.json")
    physical = read(root / "physical-recovery.json")
    if reconciliation["objects_verified"] < len(required):
        raise RuntimeError("stale reconciliation; run make verify")
    if any(
        reconciliation[k]
        for k in [
            "missing_acknowledged_keys",
            "corrupt_objects",
            "stale_versions",
            "extra_or_duplicate_keys",
        ]
    ):
        raise RuntimeError("failed final reconciliation")
    for path in ["physical-baseline.json", "physical-recovery.json"]:
        if (root / path).stat().st_mtime_ns < (
            root / "reconciliation.json"
        ).stat().st_mtime_ns:
            raise RuntimeError(
                "native audit predates final S3 readback; run make verify"
            )
    if (root / "physical-baseline.json").stat().st_mtime_ns < (
        root / "fault-history.json"
    ).stat().st_mtime_ns:
        raise RuntimeError("native audit predates a new history; run make verify")
    if physical["baseline_blocks"] != len(read(root / "physical-baseline.json")) or any(
        physical[k]
        for k in ["missing_native_replicas", "misplaced_live_blocks", "corrupt_blocks"]
    ):
        raise RuntimeError("failed native all-copy audit")
    cases = []
    for case in sorted(root.glob("fault-*")):
        if not case.is_dir():
            continue
        status = read(case / "status.json")
        value = {
            "case": case.name,
            "passed": status["passed"],
            "external_assist": (case / "manual-recovery-assist.json").exists(),
        }
        if status["passed"]:
            result = read(case / "result.json")
            value |= {
                k: result[k]
                for k in [
                    "target",
                    "mode",
                    "writer",
                    "reader",
                    "longest_read_completion_gap_seconds",
                    "phases",
                    "live_prefix",
                    "live_prefix_recovery",
                ]
            }
            value["automatic_metadata_sweeps"] = len(
                records(case / "live-repair-prefix/metadata-sweeps.jsonl")
            )
            value["native_final"] = read(case / "physical-recovery.json")
        cases.append(value)
    return {
        "fixture": read(root / "fixture.json")["id"],
        "cases": cases,
        "cumulative_ack_objects": sum(v["acked"] for v in histories),
        "cumulative_attempted_objects": len(histories),
        "optional_attempts_present": reconciliation["objects_verified"] - len(required),
        "reconciliation": reconciliation,
        "native_final": physical,
        "scope": "saved external witnesses and native proofs; failed/assisted runs retain their labels; no fresh live query",
    }


if __name__ == "__main__":
    print(
        json.dumps(
            summarize(Path(os.environ.get("RESULTS_DIR", "results/recovery"))), indent=2
        )
    )
