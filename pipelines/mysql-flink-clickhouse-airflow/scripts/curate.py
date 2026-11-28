"""Publish compact numeric evidence; retain failed attempts and raw-file provenance."""

import json
from collections import Counter
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
KINDS = [
    "read-committed-control",
    "connector-lifecycle",
    "backfill-recovery",
    "worker-recovery",
]


def load(path):
    return json.loads(path.read_text())


def records(path):
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.startswith("{")
    ]


def timing(rows, field="duration_ms"):
    values = sorted(r[field] for r in rows if field in r)
    if not values:
        return {"samples": 0}
    return {
        "samples": len(values),
        "unit": "ms",
        "mean": sum(values) / len(values),
        **{
            name: values[min(len(values) - 1, math.ceil(len(values) * q) - 1)]
            for name, q in [
                ("p50", 0.5),
                ("p95", 0.95),
                ("p99", 0.99),
                ("p999", 0.999),
                ("max", 1),
            ]
        },
        "scope": "One-sweep diagnostic nearest-rank estimates; sparse tails are not stable percentiles",
    }


def operation_summary(path):
    result = {}
    for role in ["writer", "reader"]:
        source = path / (role + ".jsonl")
        if not source.exists():
            continue
        rows = records(source)
        events = ["ack"] if role == "writer" else ["read", "recent-read"]
        for event in events:
            for phase in sorted(
                {r.get("phase", "unknown") for r in rows if r.get("event") == event}
            ):
                selected = [
                    r
                    for r in rows
                    if r.get("event") == event and r.get("phase", "unknown") == phase
                ]
                result[event + ":" + phase] = {
                    "operation_latency": timing(selected),
                    "results": {
                        name: sum(r.get("result", "ack") == name for r in selected)
                        for name in sorted({r.get("result", "ack") for r in selected})
                    },
                }
                visible = [r for r in selected if r.get("result") == "visible"]
                if visible:
                    result[event + ":" + phase][
                        "sampled_first_visibility_upper_bound"
                    ] = timing(visible, "ack_age_ms")
                    result[event + ":" + phase]["distinct_anchors"] = len(
                        {r["id"] for r in visible}
                    )
    return result


def main():
    markers = sorted(
        (ROOT / "results/cases").glob("*/status.json"),
        key=lambda p: p.stat().st_mtime_ns,
    )
    if not markers:
        raise RuntimeError("No executed case statuses")
    fixture = load(markers[-1])["fixture"]
    latest = {}
    failed = []
    for marker in markers:
        value = load(marker)
        if value["fixture"] != fixture:
            continue
        if not value["passed"]:
            failed.append({"evidence": str(marker.parent.relative_to(ROOT)), **value})
        if value["kind"] in KINDS:
            latest[value["kind"]] = marker
    if set(latest) != set(KINDS) or any(not load(p)["passed"] for p in latest.values()):
        raise RuntimeError(
            "The latest required case set has not passed; preserve and diagnose it"
        )

    cases = {}
    for kind, marker in latest.items():
        path = marker.parent
        case = {
            "status": load(marker),
            "evidence": str(path.relative_to(ROOT)),
            "operations": operation_summary(path),
            "executed_source_manifest": load(path / "source-manifest.json"),
        }
        if kind == "read-committed-control":
            case["isolation"] = load(path / "isolation.json")
        else:
            for name in [
                "reconciled",
                "durability",
                "compression",
                "replicas",
                "disk-settled",
                "reconciliation-dag",
            ]:
                case[name] = load(path / (name + ".json"))
            case["disk_phases"] = []
            for disk in sorted(path.glob("disk-*.json")):
                measured = load(disk)
                by_role = Counter()
                for volume in measured["volumes"].values():
                    by_role[volume["role"]] += volume["allocated_bytes"]
                case["disk_phases"].append(
                    {
                        "evidence": str(disk.relative_to(ROOT)),
                        "allocated_data_bytes": measured["allocated_data_bytes"],
                        "allocated_bytes_by_role": dict(by_role),
                        "external_journal_logical_bytes": measured[
                            "external_journal_logical_bytes"
                        ],
                    }
                )
            for name in [
                "pause",
                "resume",
                "schema-change",
                "backfill",
                "initial-snapshot-preserved",
                "connect-held",
                "connect-recovery",
                "taskmanager1-held",
                "taskmanager1-recovery",
            ]:
                if (path / (name + ".json")).exists():
                    case[name] = load(path / (name + ".json"))
            if kind == "connector-lifecycle":
                status = case["pause"]["status"]
                if (
                    status["connector"]["state"] != "PAUSED"
                    or not status["tasks"]
                    or any(t["state"] != "PAUSED" for t in status["tasks"])
                ):
                    raise RuntimeError("The actual paused task was not proved")
            if kind == "backfill-recovery":
                source = ROOT / case["backfill"]["evidence"]
                notices = load(source / "backfill-notifications.json")
                case["backfill_notification_counts"] = dict(
                    Counter(n["type"] for n in notices)
                )
                case["backfill_notifications"] = [
                    n
                    for n in notices
                    if n["type"] in ["STARTED", "TABLE_SCAN_COMPLETED", "COMPLETED"]
                ]
                case["backfill_dag_tasks"] = load(source / "margin_backfill-tasks.json")
        cases[kind] = case

    streams = []
    for path in sorted((ROOT / "results").glob("resources-*.jsonl")):
        with path.open() as source:
            header = json.loads(next(source))
        commands = header.get("command", [])
        if header.get("fixture") == fixture and any(
            name in commands
            for name in [
                "production-cases",
                "connector-case",
                "backfill-case",
                "worker-case",
            ]
        ):
            streams.append((path, header))
    coverage = {}
    for kind, marker in latest.items():
        begin = (marker.parent / "case.json").stat().st_mtime_ns
        finish = marker.stat().st_mtime_ns
        candidates = [
            (p, h)
            for p, h in streams
            if h["run_id"] <= begin and p.stat().st_mtime_ns + 5_000_000_000 >= finish
        ]
        if not candidates:
            raise RuntimeError("No resource stream covers executed case: " + kind)
        chosen, _ = max(candidates, key=lambda item: item[1]["run_id"])
        coverage.setdefault(chosen, []).append(kind)
    resources = []
    for stream, kinds in coverage.items():
        summary = json.loads(
            subprocess.check_output(
                [sys.executable, str(ROOT / "scripts/resource_report.py"), str(stream)],
                text=True,
            )
        )
        resources.append(
            {
                "cases_covered": kinds,
                "scope": "Entire observed Make invocation; may include other failed attempts in that invocation. These are not isolated per-case cost comparisons.",
                "summary": summary,
            }
        )
    completed = ROOT / "results" / ("down-" + fixture + ".json")
    report = {
        "classification": "Level 3 focused CDC correctness/recovery and resource diagnostics; no capacity ranking",
        "fixture": fixture,
        "contract": load(ROOT / "config/contract.json"),
        "cases": cases,
        "failed_attempts": failed,
        "resources": resources,
        "infrastructure_cleanup": [
            load(p)
            for p in sorted((ROOT / "results").glob("docker-down-*/status.json"))
            if load(p).get("fixture") == fixture
        ],
        "teardown": (
            load(completed) if completed.exists() else {"state": "not yet executed"}
        ),
        "measurement_limits": [
            "Actors are rate-limited and faults are serial. Operation timings are not saturated backend throughput or a comparison with another pipeline.",
            "Recent-read ages are sampled first-observation upper bounds, not a latency distribution over every write.",
            "Disk phases are allocated-volume snapshots, not continuous transient peaks. Each shared checkpoint/log volume is counted once; all replicas are included.",
            "Container I/O counters do not establish physical device latency, queue depth or physical power-loss durability. Inspect current-incarnation native io.stat and sampled reset-aware deltas separately.",
            "Named-volume disk snapshots exclude image layers, container writable layers, Docker-engine log storage and raw host evidence. They are backend data allocation, not whole-host occupied space.",
            "Resource windows cover the selected test invocations, including their preflight/final verification. They are not a seed-inclusive end-to-end pipeline throughput measurement; startup/seed observations remain separate raw evidence.",
            "All roles share one physical Docker VM. Metadata, source primary, JobManager and checkpoint storage remain explicit single failure points.",
        ],
    }
    destination = ROOT / "reports/QUALIFICATION.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(destination.relative_to(ROOT))


if __name__ == "__main__":
    main()
