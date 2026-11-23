"""Summarize preserved raw evidence; this command never starts or repairs storage."""

import json
import os
from datetime import datetime
from pathlib import Path

from witness import records


def read(path):
    return json.loads(path.read_text())


def summarize(root):
    cases, failed = [], []
    for case in sorted(root.glob("fault-*")):
        if not case.is_dir():
            continue
        status = read(case / "status.json")
        if not status["passed"]:
            failed.append(case.name)
            continue
        result = read(case / "result.json")
        writes = records(case / "churn.jsonl")
        reads = records(case / "reader.jsonl")
        marks = {v["phase"]: v["time"] for v in records(case / "phases.jsonl")}
        stopped = read(case / "stopped.json")
        killed = datetime.fromisoformat(stopped["State"]["FinishedAt"]).timestamp()
        if not marks["injection"] <= killed <= marks["outage"]:
            raise RuntimeError("fault and workload clocks disagree")
        acks = [v for v in writes if v["stage"] == "ack"]
        after = [v["time"] - killed for v in acks if v["time"] >= killed]
        if not after:
            raise RuntimeError("no successful write after the proven crash")
        reconciliation = read(case / "reconciliation.json")
        if any(
            reconciliation[name]
            for name in [
                "missing_acknowledged_keys",
                "corrupt_objects",
                "stale_versions",
                "extra_or_duplicate_keys",
            ]
        ):
            raise RuntimeError("failed reconciliation")
        physical = read(case / "replica-byte-audit.json")
        if physical["mismatched_objects"] or not physical["non_owner_negative_control"]:
            raise RuntimeError("failed physical-byte oracle")
        cases.append(
            {
                "case": case.name,
                "mode": result["mode"],
                "target": result["target"],
                "ack_objects": len(acks),
                "ambiguous_puts": sum(v["stage"] == "ambiguous" for v in writes),
                "successful_gets": sum(v["stage"] == "get" for v in reads),
                "get_errors": sum(v["stage"] == "get_error" for v in reads),
                "wrong_reads": sum(v["stage"] == "wrong" for v in reads),
                "outage_ack_objects": sum(
                    killed <= v["time"] < marks["repair"] for v in acks
                ),
                "outage_gets": sum(
                    v["stage"] == "get" and killed <= v["time"] < marks["repair"]
                    for v in reads
                ),
                "first_ack_after_proven_exit_seconds": min(after),
                "longest_read_completion_gap_seconds": result[
                    "longest_read_completion_gap_seconds"
                ],
                "restart_to_ready_seconds": result["ready_seconds"],
                "exit_to_native_replica_barrier_seconds": marks["after"] - killed,
                "survivors_unchanged": result["survivors_unchanged"],
                "objects_verified": reconciliation["objects_verified"],
                "direct_replica_chunk_reads": physical["direct_replica_chunk_reads"],
                "phases": result["phases"],
            }
        )
    history = read(root / "fault-history.json")
    required = {v["object"]["key"] for v in history if v["acked"]}
    for name in ["manifest", "cases-manifest", "benchmark-manifest"]:
        p = root / (name + ".json")
        if p.exists():
            required.update(v["key"] for v in read(p))
    final = read(root / "reconciliation.json")
    physical = read(root / "replica-byte-audit.json")
    if final["objects_verified"] < len(required) or physical["objects"] != len(
        required
    ):
        raise RuntimeError(
            "final verification is stale; run make verify against all histories"
        )
    if any(
        final[name]
        for name in [
            "missing_acknowledged_keys",
            "corrupt_objects",
            "stale_versions",
            "extra_or_duplicate_keys",
        ]
    ):
        raise RuntimeError("final reconciliation failed")
    return {
        "fixture": read(root / "fixture.json")["id"],
        "passed_cases": len(cases),
        "failed_cases_preserved": failed,
        "cases": cases,
        "cumulative_ack_objects": sum(v["acked"] for v in history),
        "cumulative_attempts": len(history),
        "optional_attempts_present": final["objects_verified"] - len(required),
        "final_reconciliation": final,
        "final_replica_bytes": physical,
        "scope": "one host; independent paced SDK reader/writer; saved failures remain failures",
    }


if __name__ == "__main__":
    root = Path(os.environ.get("RESULTS_DIR", "results/recovery"))
    print(json.dumps(summarize(root), indent=2))
