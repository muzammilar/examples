"""Curate serial controls and failure evidence without claiming capacity from short trials."""

import json
import hashlib
import math
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parents[1]


def records(path):
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
        "n": len(values),
        "p50_ms": statistics.median(values),
        "p95_ms": values[math.ceil(0.95 * len(values)) - 1],
        "p99_ms": values[math.ceil(0.99 * len(values)) - 1],
        "max_ms": max(values),
    }


def main():
    phases = []
    results = ROOT / "results"
    for folder in sorted(results.iterdir()):
        status = folder / "status.json"
        if not folder.is_dir() or not status.exists():
            continue
        row = {"path": str(folder.relative_to(ROOT)), **json.loads(status.read_text())}
        for file in folder.glob("*.json"):
            if file.name == "status.json":
                continue
            row[file.stem] = json.loads(file.read_text())
        volume_ref = folder / "volume-inventory-path.txt"
        if volume_ref.exists():
            inventory = Path(volume_ref.read_text().strip())
            inventory.relative_to(PARENT / "results")
            row["volume_inventory"] = [
                e
                for e in records(inventory / "volumes.jsonl")
                if e.get("event") == "volume_sizes"
            ]
        row["readbacks"] = {f.name: records(f) for f in folder.glob("*.jsonl")}
        row["checks"] = []
        groups = {}
        for file in folder.glob("*.log"):
            events = records(file)
            row["checks"] += [
                dict(log=file.name, **e)
                for e in events
                if e.get("event")
                in (
                    "verified",
                    "witness_verified",
                    "receipts_verified",
                    "fatal",
                    "configuration",
                    "resources",
                    "source",
                )
            ]
            # Live fault queries are correctness/availability evidence, never competitive trials.
            if file.name == "live-query-checks.log":
                row["live_query_results"] = {
                    "successful_verified": sum(
                        e.get("event") == "query" for e in events
                    ),
                    "errors": [e for e in events if e.get("event") == "fatal"],
                }
                continue
            manifest = next(
                (e.get("manifest", "") for e in events if e.get("event") == "source"),
                "",
            )
            reader_hash = hashlib.sha256(manifest.encode()).hexdigest()
            backend = next(
                (e.get("backend") for e in events if e.get("event") == "configuration"),
                None,
            )
            for event in events:
                if event.get("event") != "query" or event.get("warmup"):
                    continue
                key = (
                    backend,
                    event["query"],
                    event["cache"],
                    event["trial"],
                    reader_hash,
                )
                groups.setdefault(key, []).append(event["duration_ms"])
        writer = (
            records(folder / "writer.log") if (folder / "writer.log").exists() else []
        )
        row["witness_ack_latency"] = timing(
            [e["duration_ms"] for e in writer if e.get("event") == "ack"]
        )
        row["witness_ambiguous"] = [e for e in writer if e.get("event") == "ambiguous"]
        if (folder / "live-query-checks.log").exists():
            live = records(folder / "live-query-checks.log")
            row["live_query_latency"] = timing(
                [e["duration_ms"] for e in live if e.get("event") == "query"]
            )
        row["query_trials"] = [
            dict(
                backend=k[0],
                query=k[1],
                cache=k[2],
                trial=k[3],
                reader_source_manifest_sha256=k[4],
                **timing(v)
            )
            for k, v in groups.items()
        ]
        phases.append(row)
    resources = []
    for raw in sorted(results.glob("resources-*.jsonl")):
        value = json.loads(
            subprocess.check_output(
                [sys.executable, str(PARENT / "scripts/resource_report.py"), str(raw)],
                text=True,
            )
        )
        value["evidence"] = str(raw.relative_to(ROOT))
        resources.append(value)
    lifecycle = []
    owned = {p["fixture"]["id"] for p in phases if isinstance(p.get("fixture"), dict)}
    for folder in (PARENT / "results").glob("docker-down-*"):
        fixture = folder / "fixture.json"
        status = folder / "status.json"
        cleanup = folder / "cleanup.json"
        if (
            fixture.exists()
            and status.exists()
            and json.loads(fixture.read_text())["id"] in owned
        ):
            lifecycle.append(
                {
                    "path": str(folder.relative_to(PARENT)),
                    **json.loads(status.read_text()),
                    "cleanup": (
                        json.loads(cleanup.read_text()) if cleanup.exists() else None
                    ),
                }
            )
    report = {
        "performance_depth": 3,
        "lifecycle": lifecycle,
        "phases": phases,
        "resources": resources,
        "limitations": [
            "Short serial query trials are diagnostic, not sustained or cluster capacity benchmarks.",
            "Three trials, 30 measured warm queries per family; small-sample p99 is close to the maximum.",
            "New connection does not clear server or OS caches; physical disk-cold is unrun.",
            "Total database caps match; single-query execution/parallelism and preprocessing architectures differ.",
            "All processing/serving costs must be attributed separately; serving latency alone cannot rank complete pipelines.",
            "Docker samples omit some first/last tails, short actors and host/build costs. Native counters may overlap.",
            "One VM and simulated replica roles do not establish independent-host or physical power-loss durability.",
        ],
    }
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/QUALIFICATION.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(ROOT / "reports/QUALIFICATION.json")


if __name__ == "__main__":
    main()
