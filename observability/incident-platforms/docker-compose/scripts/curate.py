"""Export compact evidence from a named completed case; never copy raw bodies."""

import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(path.read_text())


def main():
    identifier = sys.argv[1]
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Expected a preserved case ID")
    case = ROOT / "results/cases" / identifier
    result = {
        "case": identifier,
        "metadata": load(case / "case.json"),
        "fixture": load(case / "fixture.json"),
    }
    for name in [
        "status.json",
        "source.json",
        "monolith-recovery.json",
        "disk-before-ingest.json",
        "disk-after-actors.json",
    ]:
        if (case / name).exists():
            result[name] = load(case / name)
    if (case / "monolith-killed.json").exists():
        row = load(case / "monolith-killed.json")
        result["proven_fault"] = {
            "state": row["State"],
            "image": row["Image"],
            "reference": row["Config"]["Image"],
            "mounts": row["Mounts"],
        }
    if (case / "failure.txt").exists():
        result["failure"] = (case / "failure.txt").read_text()
    result["readbacks"] = {}
    for base in [case, ROOT / "results/restart-audits"]:
        for report in base.rglob("readback.json"):
            value = load(report)
            if value.get("fixture", {}).get("run") == identifier:
                result["readbacks"][str(report.relative_to(ROOT))] = value
    for resource in sys.argv[2:]:
        path = ROOT / resource
        output = subprocess.check_output(
            [
                sys.executable,
                str(ROOT / "scripts/resource_report.py"),
                str(path),
                "live-publish:,fault:,recovery:",
            ],
            text=True,
        )
        result.setdefault("resources", []).append(json.loads(output))
    result["raw_witnesses"] = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(case.rglob("requests.jsonl"))
    }
    result["source_batches"] = {}
    for path in sorted(case.rglob("requests.jsonl")):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        responses = [row for row in rows if row["event"] == "response"]
        summary = {
            "timing_scope": "HTTP batch admission, not durable commit, visibility or per-record latency",
            "timer": "Go monotonic duration, reported ms; nanosecond representation does not establish nanosecond accuracy",
        }
        for outcome in sorted({row["outcome"] for row in responses}):
            group = [row for row in responses if row["outcome"] == outcome]
            durations = sorted(row.get("http_ms", 0) for row in group)
            summary[outcome] = {
                "batches": len(group),
                "http_ms_mean": sum(durations) / len(durations),
                "raw_bytes": sum(row["raw_bytes"] for row in group),
                "gzip_bytes": sum(row["wire_bytes"] for row in group),
            }
            for percentile in [50, 95, 99, 99.9]:
                summary[outcome]["http_ms_p" + str(percentile)] = durations[
                    max(0, math.ceil(len(durations) * percentile / 100) - 1)
                ]
            summary[outcome]["source_wire_compression_ratio"] = (
                summary[outcome]["raw_bytes"] / summary[outcome]["gzip_bytes"]
            )
        result["source_batches"][path.parent.name] = summary
    destination = ROOT / "reports" / (identifier + ".json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(destination)


if __name__ == "__main__":
    main()
