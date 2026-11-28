"""Publish before/after recovery evidence without erasing a failed original run."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from curate import latency, records
from recovery import stamp

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(path.read_text())


def compact_readback(path):
    value = load(path)
    ids = value["result"].pop("missing_acknowledged_ids")
    value["result"].update(
        missing_acknowledged_count=len(ids),
        missing_id_first=ids[0] if ids else None,
        missing_id_last=ids[-1] if ids else None,
        missing_ids_sha256=hashlib.sha256(json.dumps(ids).encode()).hexdigest(),
    )
    return value


def main():
    cases = []
    for case in sorted((ROOT / "results/cases").glob("*/case.json")):
        metadata = load(case)
        if metadata["kind"] != "live-recovery":
            continue
        path = case.parent
        if not (path / "status.json").exists():
            raise RuntimeError("Cannot curate an unfinished live case")
        signals = {}
        for backend in ["greptime", "victoria"]:
            for signal in ["metrics", "logs"]:
                directory = path / backend / signal
                journal = directory / "requests.jsonl"
                if not journal.exists():
                    continue
                rows = records(journal)
                responses = [r for r in rows if r["event"] == "response"]
                reads = records(directory / "reads.jsonl")
                raw = sum(r["raw_bytes"] for r in responses)
                wire = sum(r["wire_bytes"] for r in responses)
                fault = None
                killed = path / (backend + "-killed.json")
                restarted = path / (backend + "-recovery.json")
                if killed.exists() and restarted.exists():
                    low = stamp(load(killed)["State"]["FinishedAt"])
                    high = stamp(load(restarted)["started_at"])
                    fault = {
                        "start": low.isoformat(),
                        "end": high.isoformat(),
                        "acknowledged_records_during_held_outage": sum(
                            r["last_exclusive"] - r["first"]
                            for r in responses
                            if r["outcome"] == "accepted"
                            and low <= stamp(r["ended_at"]) < high
                        ),
                        "unacknowledged_responses_during_held_outage": sum(
                            r["outcome"] != "accepted"
                            and low <= stamp(r["ended_at"]) < high
                            for r in responses
                        ),
                    }
                entry = {
                    "held_fault_observations": fault,
                    "configuration": load(directory / "configuration.json"),
                    "history_sha256": hashlib.sha256(journal.read_bytes()).hexdigest(),
                    "acknowledged_records": sum(
                        r["last_exclusive"] - r["first"]
                        for r in responses
                        if r["outcome"] == "accepted"
                    ),
                    "response_outcomes": {
                        name: sum(r["outcome"] == name for r in responses)
                        for name in sorted({r["outcome"] for r in responses})
                    },
                    "http_request_latency_diagnostic": latency(
                        [r["http_ms"] for r in responses]
                    ),
                    "wire_compression": {
                        "raw_bytes": raw,
                        "wire_bytes": wire,
                        "ratio": raw / wire if wire else None,
                    },
                    "read_observations": {
                        kind: {
                            result: sum(
                                r["kind"] == kind and r["result"] == result
                                for r in reads
                            )
                            for result in sorted(
                                {r["result"] for r in reads if r["kind"] == kind}
                            )
                        }
                        for kind in ["old", "recent"]
                    },
                    "read_request_latency_diagnostic": latency(
                        [r["duration_ms"] for r in reads]
                    ),
                    "reconciliations": {
                        f.name: compact_readback(f)
                        for f in directory.glob("reconciliation-*.json")
                    },
                    "replay": (
                        [
                            r
                            for r in records(directory / "replay-first.log")
                            if r.get("event") == "external-witness-replay"
                        ]
                        if (directory / "replay-first.log").exists()
                        else []
                    ),
                }
                signals[backend + ":" + signal] = entry
        cases.append(
            {
                "case": metadata,
                "fixture": load(path / "fixture.json"),
                "status": load(path / "status.json"),
                "failure": (
                    (path / "failure.txt").read_text()
                    if (path / "failure.txt").exists()
                    else None
                ),
                "signals": signals,
                "recovery_times": {
                    f.stem: load(f) for f in path.glob("*-recovery.json")
                },
                "data_snapshots": {
                    str(f.relative_to(path)): load(f)
                    for f in path.rglob("*-disk-*.json")
                },
                "native_resources": {
                    str(f.relative_to(path)): load(f)
                    for f in path.rglob("*-native-cgroup-*.json")
                },
                "workload_source": load(path / "source-manifest.json"),
            }
        )
    resources = []
    for stream in sorted((ROOT / "results").glob("resources-*.jsonl")):
        with stream.open() as source:
            header = json.loads(next(source))
        if not any(
            verb in header["command"]
            for verb in [
                "live-case",
                "metrics-fault-case",
                "logs-fault-case",
                "witness-case",
            ]
        ):
            continue
        resources.append(
            json.loads(
                subprocess.check_output(
                    [
                        sys.executable,
                        str(ROOT / "scripts/resource_report.py"),
                        str(stream),
                    ],
                    text=True,
                )
            )
        )
    report = {
        "scope": "Focused single-node live SIGKILL/retained-storage recovery diagnostics, not sustained performance qualification",
        "cases": cases,
        "observed_resource_windows": resources,
        "limits": [
            "Original failed status remains failed after an external witness replay.",
            "Victoria HTTP admission and Greptime synchronous WAL acknowledgements have different persistence contracts.",
            "Closed-loop writer includes 200ms think time; request latency is per batch, not per record.",
            "Five seconds is the recent-reader rotation diagnostic, not a universal visibility SLA.",
            "Resource windows include retained-history preflight and backend switches, not isolated per-engine cost.",
            "Wire gzip is not on-disk codec compression. Native codec ratio, physical device latency and unexposed disk IOPS are unavailable.",
            "One host and retained volumes do not test permanent disk/host loss or physical power failure.",
        ],
    }
    destination = ROOT / "reports/RECOVERY.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(destination)


if __name__ == "__main__":
    main()
