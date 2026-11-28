"""Curate bounded query/batch evidence and later measured cells, including failures."""

from datetime import timedelta
import hashlib
import json
import re
from pathlib import Path
import statistics
import subprocess
import sys

from curate import latency, records
from recovery import stamp

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(path.read_text())


def summary(values):
    if not values:
        return {"samples": 0}
    quartiles = (
        statistics.quantiles(values, n=4, method="inclusive")
        if len(values) > 1
        else [values[0]] * 3
    )
    return {
        "samples": len(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "iqr": quartiles[2] - quartiles[0],
    }


def journal_summary(directory, window=None):
    responses = [
        r for r in records(directory / "requests.jsonl") if r["event"] == "response"
    ]
    if window:
        responses = [
            r
            for r in responses
            if window[0] <= stamp(r["started_at"]) and stamp(r["ended_at"]) <= window[1]
        ]
    raw = sum(r["raw_bytes"] for r in responses)
    wire = sum(r["wire_bytes"] for r in responses)
    accepted = sum(
        r["last_exclusive"] - r["first"]
        for r in responses
        if r["outcome"] == "accepted"
    )
    result = {
        "accepted_records": accepted,
        "response_count": len(responses),
        "unacknowledged_responses": sum(r["outcome"] != "accepted" for r in responses),
        "http_request_ms": latency([r["http_ms"] for r in responses]),
        "construction_encoding_ms": latency([r["encoding_ms"] for r in responses]),
        "raw_encoded_bytes": raw,
        "wire_bytes": wire,
        "wire_ratio": raw / wire if wire else None,
        "history_sha256": hashlib.sha256(
            (directory / "requests.jsonl").read_bytes()
        ).hexdigest(),
    }
    if window:
        seconds = (window[1] - window[0]).total_seconds()
        result.update(
            measurement_seconds=seconds,
            accepted_records_per_second=accepted / seconds,
            measurement_start=window[0].isoformat(),
            measurement_end=window[1].isoformat(),
        )
        for key in ["http_request_ms", "construction_encoding_ms"]:
            result[key][
                "qualification"
            ] = "Per-request samples from the fixed measurement window; 1000 records/request, not per-record visibility latency"
    return result


def native_storage(path):
    snapshots = {}
    for source in sorted(path.glob("*-native-*.prom")):
        fields = []
        for line in source.read_text().splitlines():
            if re.match(
                r"^(vl_(uncompressed_data_size_bytes|compressed_data_size_bytes|data_size_bytes|storage_rows)|vm_(data_size_bytes|rows))([{ ])",
                line,
            ):
                fields.append(line)
        entry = {"evidence": str(source.relative_to(ROOT)), "storage_gauges": fields}
        if source.name.startswith("victoria-logs"):
            totals = {"uncompressed": 0, "compressed": 0}
            for line in fields:
                match = re.fullmatch(
                    r'vl_(uncompressed|compressed)_data_size_bytes\{type="storage/(?:small|big)"\} ([0-9.eE+-]+)',
                    line,
                )
                if match:
                    totals[match[1]] += float(match[2])
            entry["on_disk_data_codec"] = {
                **totals,
                "uncompressed_to_compressed_ratio": (
                    totals["uncompressed"] / totals["compressed"]
                    if totals["compressed"]
                    else None
                ),
                "scope": "Native small+big data parts, excluding in-memory parts, indexdb, WAL and filesystem allocation; includes the seed and new data, not just the measured five-minute slice",
            }
        else:
            entry["on_disk_data_codec"] = {
                "state": "unavailable",
                "reason": "No directly comparable raw/compressed storage-byte pair established from the inspected pinned native metrics; use separately scoped allocation snapshots.",
            }
        snapshots[source.name] = entry
    return snapshots


def measured_resources(stream, header, measured):
    # Keep the sampled common window of both actors; no warmup, setup or full
    # body-oracle cost can silently enter a per-engine resource comparison.
    windows = {}
    for cell in measured:
        for backend in ["greptime", "victoria"]:
            signals = [
                cell["signals"].get(backend + ":" + signal)
                for signal in ["metrics", "logs"]
            ]
            if any(s is None for s in signals):
                continue
            low = max(stamp(s["measurement_start"]) for s in signals)
            high = min(stamp(s["measurement_end"]) for s in signals)
            key = "measured-baseline:" + cell["case"]["run"] + ":" + backend
            windows[key] = (cell, backend, low, high)
    if not windows:
        return
    directory = ROOT / "results/resource-slices" / stream.stem
    directory.mkdir(parents=True, exist_ok=True)
    handles = {}
    configurations = {}
    try:
        for line in stream.open():
            row = json.loads(line)
            if row["type"] == "environment":
                continue
            for actor in row["actors"]:
                if "configuration" in actor:
                    configurations[actor["id"]] = actor["configuration"]
            key = row["phase"]
            if key not in windows:
                continue
            cell, backend, low, high = windows[key]
            if not low <= stamp(row["utc"]) <= high:
                continue
            if key not in handles:
                file = (
                    directory / (cell["case"]["run"] + "-" + backend + ".jsonl")
                ).open("w")
                file.write(
                    json.dumps(
                        {
                            **header,
                            "measurement_scope": key,
                            "common_start": low.isoformat(),
                            "common_end": high.isoformat(),
                        }
                    )
                    + "\n"
                )
                handles[key] = file
            for actor in row["actors"]:
                if actor["id"] in configurations:
                    actor["configuration"] = configurations[actor["id"]]
            handles[key].write(json.dumps(row) + "\n")
    finally:
        for file in handles.values():
            file.close()
    for key, file in handles.items():
        cell, backend, low, high = windows[key]
        result = json.loads(
            subprocess.check_output(
                [sys.executable, str(ROOT / "scripts/resource_report.py"), file.name],
                text=True,
            )
        )
        cell.setdefault("measured_resources", {})[backend] = {
            "common_window_s": (high - low).total_seconds(),
            "summary": result,
            "scope": "Intersection of both actors' measured windows; native Docker counter sample intervals exclude unsampled edges; no setup, warmup or fenced full readback",
        }


def main():
    queries = []
    for status in sorted((ROOT / "results/queries").glob("*/status.json")):
        trials = []
        for report in sorted(status.parent.glob("*/*/*/queries.json")):
            value = load(report)
            samples = value["samples"]
            trials.append(
                {
                    "trial": int(report.relative_to(status.parent).parts[0]),
                    "backend": value["backend"],
                    "query": value["query"],
                    "first_query_ms": samples[0]["request_decode_ms"],
                    "warm_ms": summary([s["request_decode_ms"] for s in samples[1:]]),
                    "all_answers_correct": all(s["correct"] for s in samples),
                    "raw_samples": samples,
                    "source_report": str(report.relative_to(ROOT)),
                }
            )
        aggregates = {}
        for backend in ["greptime", "victoria"]:
            for name in ["metric-count", "metric-sum", "log-count"]:
                cells = [
                    t for t in trials if t["backend"] == backend and t["query"] == name
                ]
                aggregates[backend + ":" + name] = {
                    "first_query_ms": summary([t["first_query_ms"] for t in cells]),
                    "warm_trial_median_ms": summary(
                        [
                            t["warm_ms"]["median"]
                            for t in cells
                            if t["warm_ms"]["samples"]
                        ]
                    ),
                }
        queries.append(
            {"status": load(status), "trials": trials, "summary": aggregates}
        )
    batches = []
    measured = []
    for case in sorted((ROOT / "results/cases").glob("*/case.json")):
        metadata = load(case)
        if metadata["kind"] not in ["batch-screen", "measured-baseline"]:
            continue
        path = case.parent
        entry = {
            "case": metadata,
            "fixture": load(path / "fixture.json"),
            "status": (
                load(path / "status.json")
                if (path / "status.json").exists()
                else {"passed": False, "state": "unfinished"}
            ),
            "signals": {},
        }
        for backend in ["greptime", "victoria"]:
            for signal in ["metrics", "logs"]:
                directory = path / backend / signal
                if not (directory / "requests.jsonl").exists():
                    continue
                configuration = load(directory / "configuration.json")
                window = None
                if metadata["kind"] == "measured-baseline":
                    low = stamp(configuration["started_at"]) + timedelta(
                        seconds=configuration["benchmark_warmup_s"]
                    )
                    window = (
                        low,
                        low
                        + timedelta(seconds=configuration["benchmark_measurement_s"]),
                    )
                result = journal_summary(directory, window)
                result["configuration"] = configuration
                result["native_pre_exit"] = (
                    [
                        r
                        for r in records(directory / "actor.log")
                        if r.get("event") == "native-cgroup-before-exit"
                    ]
                    if (directory / "actor.log").exists()
                    else []
                )
                result["reconciliation"] = {
                    f.name: load(f) for f in directory.glob("reconciliation-*.json")
                }
                result["completion"] = (
                    [
                        r
                        for r in records(directory / "actor.log")
                        if r.get("event") == "live-completed"
                    ]
                    if (directory / "actor.log").exists()
                    else []
                )
                if metadata["kind"] == "batch-screen":
                    result["admission"] = [
                        r
                        for r in records(directory / "push.log")
                        if r.get("event") == "admission-complete"
                    ]
                entry["signals"][backend + ":" + signal] = result
        entry["workload_source_manifest"] = load(path / "source-manifest.json")
        entry["native_backend_cgroups"] = {
            f.name: load(f) for f in path.glob("*-native-cgroup-*.json")
        }
        entry["native_storage"] = native_storage(path)
        entry["disk_snapshots"] = {p.name: load(p) for p in path.glob("*-disk-*.json")}
        (batches if metadata["kind"] == "batch-screen" else measured).append(entry)
    windows = []
    for stream in sorted((ROOT / "results").glob("resources-*.jsonl")):
        with stream.open() as source:
            header = json.loads(next(source))
        if not any(
            verb in header["command"]
            for verb in ["query-case", "batch-case", "benchmark-case"]
        ):
            continue
        if "benchmark-case" in header["command"]:
            measured_resources(stream, header, measured)
        windows.append(
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
    matrices = [
        {
            "configuration": load(p),
            "status": (
                load(p.parent / "status.json")
                if (p.parent / "status.json").exists()
                else {"passed": False, "state": "unfinished"}
            ),
        }
        for p in sorted((ROOT / "results/benchmarks").glob("*/matrix.json"))
    ]
    completed_teardowns = []
    for receipt in sorted((ROOT / "results/teardowns").glob("*/status.json")):
        value = load(receipt)
        if not any(c["case"]["run"] in value.get("cases", []) for c in measured):
            continue
        completed_teardowns.append(
            {
                "evidence": str(receipt.relative_to(ROOT)),
                "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
                "verified": value["verified"],
                "owned_resources_absent": value["owned_resources_absent"],
                "cases": value["cases"],
                "witness_count": len(value["witnesses"]),
                "removed_workload_image_id": value["removed_workload_image_id"],
            }
        )

    output = {
        "completed_teardowns": completed_teardowns,
        "matrices": matrices,
        "equal_acknowledgement_durability": False,
        "overall_winner": None,
        "scope": "Level-3 bounded screens and paced healthy ingestion; different persistence contracts, no overall capacity/durability winner",
        "queries": queries,
        "batch_screens": batches,
        "measured_cells": measured,
        "resource_windows": windows,
        "limits": [
            "Queries use 3 alternating fresh-role-restart trials with 30 identical warm queries after the first. Host page cache is retained; these samples do not establish sustained query capacity or a p99 tail.",
            "Batch screens use 10000 records/signal/cell, not sustained capacity; timing is per batch and includes the declared client boundary.",
            "Healthy ingestion has 60s warmup/300s measurement; two closed-loop workers each wait 200ms after a 1000-record request. Paced completed rates are not maximum capacity.",
            "Each measured trial starts with fresh volumes and an identical million-record logical seed; system order alternates. Namespace IDs differ; seed, timestamps and payloads are fixed.",
            "Full invocation resource windows include preflight, rebuild, setup, transitions and readback; do not rank per-engine resource cost from these aggregate totals.",
            "Native VictoriaLogs small/big-part codec counters are reported separately. Comparable Greptime/VictoriaMetrics codec ratios and unavailable device IOPS/latency remain gaps; wire compression is distinct.",
        ],
    }
    (ROOT / "reports/COMPARISON.json").write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
