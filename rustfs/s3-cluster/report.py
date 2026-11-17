"""Offline report from retained witnesses; never query or repair the database."""

import json
import os
from pathlib import Path

from native_health import heal_complete, member_identity, replacement_complete


def read(path):
    return json.loads(path.read_text())


def summarize(root):
    required = set()
    for name in ["manifest", "cases-manifest", "benchmark-manifest"]:
        path = root / (name + ".json")
        if path.exists():
            required.update(v["key"] for v in read(path))
    histories = read(root / "fault-history.json")
    required.update(v["object"]["key"] for v in histories if v["acked"])
    reconciliation = read(root / "reconciliation.json")
    if reconciliation["objects_verified"] < len(required) or any(
        reconciliation[k]
        for k in [
            "missing_acknowledged_keys",
            "corrupt_objects",
            "stale_versions",
            "extra_or_duplicate_keys",
        ]
    ):
        raise RuntimeError("failed or stale complete-body reconciliation")
    for name in [
        "physical-recovery",
        "native-health",
        "heal-status",
    ]:
        if (root / (name + ".json")).stat().st_mtime_ns < (
            root / "fault-history.json"
        ).stat().st_mtime_ns:
            raise RuntimeError("native proof predates history; run make verify")
    for name in ["physical-recovery", "native-health"]:
        if (root / (name + ".json")).stat().st_mtime_ns < (
            root / "reconciliation.json"
        ).stat().st_mtime_ns:
            raise RuntimeError(
                "native proof predates full body readback; run make verify"
            )
    physical = read(root / "physical-recovery.json")
    if (
        physical["objects"] != len(required)
        or physical["physical_metadata_copies"] != 4 * len(required)
        or physical["missing_metadata_copies"]
        or physical["changed_payload_fingerprints"]
    ):
        raise RuntimeError("failed or stale native physical evidence")
    if not heal_complete(
        read(root / "heal-status.json"), minimum_objects=len(required)
    ):
        raise RuntimeError("native heal is unfinished")
    native = read(root / "native-health.json")
    if member_identity(native, repaired=True) is None or not all(
        replacement_complete(v["replacement"]) for v in native.values()
    ):
        raise RuntimeError("native health/replacement is unfinished or unknown")
    fixture = read(root / "fixture.json")
    identity = member_identity(native, repaired=True)
    baseline = read(root / "physical-baseline.json")
    if (
        identity != fixture["native_identity"]
        or read(root / "native-formats.json") != identity
        or baseline["deployment"] != identity["deployment"]
    ):
        raise RuntimeError("native/physical identities differ from the bound fixture")
    if physical["retained_immutable_payload_files"] != sum(
        len(parts) for slots in baseline["parts"].values() for parts in slots.values()
    ):
        raise RuntimeError("physical proof does not cover the retained golden files")
    cases = []
    for case in sorted(root.glob("fault-*")):
        if not case.is_dir():
            continue
        status = read(case / "status.json")
        if status.get("reason") == "case not completed":
            raise RuntimeError("fault case has not completed: " + case.name)
        value = {"case": case.name, **status}
        if status["passed"]:
            result = read(case / "result.json")
            value |= {
                k: result[k]
                for k in [
                    "mode",
                    "target",
                    "writer",
                    "reader",
                    "phases",
                    "longest_read_completion_gap_seconds",
                    "live_prefix",
                    "live_prefix_recovery",
                    "live_prefix_readback",
                ]
            }
        cases.append(value)
    return {
        "fixture": read(root / "fixture.json")["id"],
        "cases": cases,
        "cumulative_attempts": len(histories),
        "cumulative_ack_objects": sum(v["acked"] for v in histories),
        "optional_attempts_present": reconciliation["objects_verified"] - len(required),
        "reconciliation": reconciliation,
        "physical": physical,
        "native_heal_counters": read(root / "heal-status.json")["outcome"]["counters"],
        "scope": "external whole-body readback, four metadata paths and retained immutable payload fingerprints; inline decoding/canonical repair remains native",
    }


if __name__ == "__main__":
    print(
        json.dumps(
            summarize(Path(os.environ.get("RESULTS_DIR", "results/recovery"))), indent=2
        )
    )
