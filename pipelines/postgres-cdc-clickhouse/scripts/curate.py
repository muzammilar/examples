"""Produce a compact, evidence-linked report without copying raw histories."""

import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(path.read_text())


def timings(rows):
    values = sorted(row["duration_ms"] for row in rows)
    if not values:
        return {"samples": 0}
    return {
        "samples": len(values),
        "unit": "ms",
        "average": sum(values) / len(values),
        **{
            name: values[min(len(values) - 1, math.ceil(len(values) * fraction) - 1)]
            for name, fraction in [
                ("p50", 0.5),
                ("p95", 0.95),
                ("p99", 0.99),
                ("max", 1),
            ]
        },
    }


def main():
    cases = sorted((ROOT / "results").glob("test-*/status.json"))
    passed = [p for p in cases if load(p)["passed"]]
    if not passed:
        raise RuntimeError("No passing recovery case; preserve the failed evidence")
    case = passed[-1].parent
    status = load(case / "status.json")
    actors = {}
    for role, event in [("writer", "ack"), ("reader", "read")]:
        rows = [
            json.loads(line)
            for line in (case / (role + ".jsonl")).read_text().splitlines()
            if line.startswith("{")
        ]
        actors[role] = {}
        for phase in sorted({r.get("phase") for r in rows if r.get("event") == event}):
            selected = [
                r for r in rows if r.get("event") == event and r.get("phase") == phase
            ]
            actors[role][phase] = {
                "timing": timings(selected),
                "results": {
                    result: sum(r.get("result", "ack") == result for r in selected)
                    for result in sorted({r.get("result", "ack") for r in selected})
                },
            }
    observations = [p.parent for p in (ROOT / "results").glob("*/native-cgroup.json")]
    observations = sorted(
        p
        for p in observations
        if (p / "reconciled.json").exists()
        and load(p / "reconciled.json")["replicas"][0]["body_sha256"]
        == load(case / "reconciled.json")["replicas"][0]["body_sha256"]
    )
    if not observations:
        raise RuntimeError("No complete post-recovery resource/accounting observation")
    observed = observations[-1]
    streams = []
    for path in sorted((ROOT / "results").glob("resources-*.jsonl")):
        with path.open() as source:
            header = json.loads(next(source))
        if header.get("fixture") == status[
            "fixture"
        ] and "snapshot-recovery" in header.get("command", []):
            streams.append(path)
    if not streams:
        raise RuntimeError("No resource samples for this fixture")
    resource_summary = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                str(ROOT / "scripts/resource_report.py"),
                str(streams[-1]),
            ],
            text=True,
        )
    )
    source_settings = load(observed / "source-settings.json")
    if any(
        source_settings[key] != "on"
        for key in ["synchronous_commit", "fsync", "full_page_writes"]
    ):
        raise RuntimeError("Source durability differs from the contract")
    for host, settings in load(observed / "clickhouse-settings.json").items():
        effective = {row["name"]: row["value"] for row in settings["durability"]}
        if (
            effective.get("insert_quorum") != "2"
            or len(settings["table_durability"]) != 3
        ):
            raise RuntimeError(
                "Target quorum/table settings differ from the contract: " + host
            )
        if any(
            row["fsync_after_insert"] != "1" or row["fsync_part_directory"] != "1"
            for row in settings["table_durability"]
        ):
            raise RuntimeError(
                "Target table fsync settings differ from the contract: " + host
            )
    manifest = {}
    for pattern in [
        "orders-workload/*.go",
        "orders-workload/go.*",
        "orders-workload/Dockerfile",
        "scripts/*.py",
        "config/*",
        "sql/*",
        "compose.yaml",
        "Makefile",
    ]:
        for path in ROOT.glob(pattern):
            manifest[str(path.relative_to(ROOT))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    report = {
        "classification": "Level 3 focused recovery diagnostics; one successful sweep, no capacity ranking or physical-host HA claim",
        "status": status,
        "application_teardown": load(
            ROOT / "results" / ("down-" + status["fixture"] + ".json")
        ),
        "infrastructure_teardown": [
            load(p)
            for p in (ROOT / "results").glob("docker-down-*/status.json")
            if load(p).get("fixture") == status["fixture"]
        ],
        "evidence": str(case.relative_to(ROOT)),
        "failed_cases": [
            {"evidence": str(p.parent.relative_to(ROOT)), **load(p)}
            for p in cases
            if not load(p)["passed"]
        ],
        "snapshot_overlap": {
            name: load(case / (name + ".json"))
            for name in ["snapshot-overlap-start", "snapshot-live-progress"]
        },
        "recovery": {
            name: load(case / (name + ".json"))
            for name in [
                "held",
                "recovery-timing",
                "snapshot-counts-after",
                "source-offsets-before",
                "source-offsets-after",
            ]
        },
        "exact_reconciliation": load(case / "reconciled.json"),
        "operation_timings": actors,
        "resources": resource_summary,
        "native_accounting": {
            name: load(observed / (name + ".json"))
            for name in [
                "native-cgroup",
                "compression",
                "disk-space",
                "source-settings",
                "clickhouse-settings",
                "replicas",
            ]
        },
        "measurement_gaps": [
            "Unrecognized raw phase labels remain separate buckets in operation_timings; they are not silently normalized. The current controller publishes phases atomically.",
            "Resource time series starts before snapshot/recovery, after source seeding. Native cumulative counters/high-water cover earlier work only for unrestarted roles; setup/seed phase averages are unavailable.",
            "Allocated disk space and compression are settled snapshots, not measured transient peak disk usage. No physical-device I/O latency/queue-depth or power-loss measurement.",
            "Rate-limited mutation/reader timings are diagnostics from one bounded successful sweep; they do not establish sustainable throughput or comparative tail latency.",
        ],
        "source_manifest_scope": "Current checkout at curation, including post-run atomic phase publication fix. Actor logs separately contain the actual Go build source manifest.",
        "source_sha256": manifest,
    }
    destination = ROOT / "reports/QUALIFICATION.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(destination.relative_to(ROOT))


if __name__ == "__main__":
    main()
