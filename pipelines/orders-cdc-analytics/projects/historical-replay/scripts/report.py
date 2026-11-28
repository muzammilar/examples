"""Compact evidence inventory; preserve failed phases and independent recovery."""

import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parents[1]


def rows(path):
    result = []
    for line in path.read_text().splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


def timing(values):
    if not values:
        return None
    values = sorted(values)
    return {
        "count": len(values),
        "p50_ms": statistics.median(values),
        "p95_ms": values[math.ceil(0.95 * len(values)) - 1],
        "p99_ms": values[math.ceil(0.99 * len(values)) - 1],
        "max_ms": max(values),
        "scope": "diagnostic single trial; nearest-rank percentiles; maxima retained",
    }


def main():
    phases = []
    for path in sorted((ROOT / "results").glob("replay-*")):
        if not (path / "status.json").exists():
            continue
        phase = {
            "path": str(path.relative_to(ROOT)),
            **json.loads((path / "status.json").read_text()),
        }
        for name in (
            "identity",
            "budgets",
            "snapshot-oracle",
            "live-progress",
            "fault",
            "topology-verdict",
        ):
            file = path / (name + ".json")
            if file.exists():
                phase[name] = json.loads(file.read_text())
        events = {p.name: rows(p) for p in path.glob("*.log")}
        phase["native_transactions"] = [
            dict(log=name, **r)
            for name, records in events.items()
            for r in records
            if r.get("event") in ("transaction", "commit_response", "race_source")
        ]
        phase["exact_checks"] = [
            dict(log=name, **r)
            for name, events in events.items()
            for r in events
            if r.get("event") in ("verified", "receipts_verified")
        ]
        writer = events.get("writer.log", [])
        reader = events.get("reader.log", [])
        phase["source_ack_transactions"] = timing(
            [r["duration_ms"] for r in writer if r.get("event") == "ack"]
        )
        phase["source_ambiguous"] = [r for r in writer if r.get("event") == "ambiguous"]
        phase["warm_historical_reads"] = {
            t: timing(
                [
                    r["duration_ms"]
                    for r in reader
                    if r.get("event") == "read" and r["table"] == t
                ]
            )
            for t in ("checkpointed", "asynchronous")
        }
        phase["read_errors"] = [
            r for r in reader if r.get("event") in ("read_error", "fatal")
        ]
        phase["spark"] = [
            r for r in events.get("spark.log", []) if r.get("event") == "spark_complete"
        ]
        if "identity" in phase:
            data = PARENT / phase["identity"]["data"]
            phase["prepared_batches"] = [
                json.loads(p.read_text()) for p in sorted(data.glob("prepare-*.json"))
            ]
        phases.append(phase)
    resources = []
    for raw in sorted((ROOT / "results").glob("resources-*.jsonl")):
        data = json.loads(
            subprocess.check_output(
                [sys.executable, str(PARENT / "scripts/resource_report.py"), str(raw)],
                text=True,
            )
        )
        data["evidence"] = str(raw.relative_to(ROOT))
        resources.append(data)
    recoveries = {
        p.name: rows(p) for p in sorted((ROOT / "results").glob("recovery-*.log"))
    }
    fixtures = {
        phase["identity"]["fixture"]["id"] for phase in phases if "identity" in phase
    }
    volumes = []
    for path in sorted((PARENT / "results").glob("resources-*")):
        file = path / "volumes.jsonl"
        fixture = path / "fixture.json"
        if (
            file.exists()
            and fixture.exists()
            and json.loads(fixture.read_text())["id"] in fixtures
        ):
            volumes.append({"path": str(path.relative_to(PARENT)), "rows": rows(file)})
    lifecycle = []
    for mode in ("down", "docker-down"):
        for path in sorted((PARENT / "results").glob(mode + "-*")):
            fixture = path / "fixture.json"
            status = path / "status.json"
            if (
                fixture.exists()
                and status.exists()
                and json.loads(fixture.read_text())["id"] in fixtures
            ):
                lifecycle.append(
                    {
                        "path": str(path.relative_to(PARENT)),
                        **json.loads(status.read_text()),
                    }
                )
    result = {
        "performance_depth": 3,
        "lifecycle": lifecycle,
        "phases": phases,
        "independent_original_case_recovery": recoveries,
        "resources": resources,
        "volume_inventories": volumes,
        "source_files_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in ROOT.rglob("*")
            if p.is_file()
            and not any(
                part in ("results", ".build", "__pycache__", "reports")
                for part in p.relative_to(ROOT).parts
            )
        },
        "limitations": [
            "Single Docker VM; no independent-host or power-loss proof.",
            "Spark local[1] is a bounded historical recomputation, not Spark cluster capacity.",
            "Separate sink transactions; no atomic publication across both tables.",
            "CSV reference compression is not native database compression; native ZSTD ratio is unavailable.",
            "Docker observations omit some startup/exit tails, short actors and host/build costs; do not sum overlapping counters.",
            "Observer records initial container StartedAt only; restart attribution uses native fault evidence and counter-reset detection, not complete per-epoch sampling.",
            "Physical disk service latency/cold-cache behavior and hard-delete/backfill races remain unrun.",
        ],
    }
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/QUALIFICATION.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(ROOT / "reports/QUALIFICATION.json")


if __name__ == "__main__":
    main()
