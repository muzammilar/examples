"""Offline operation summaries for this example's preserved recovery journals."""

from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics


def read_records(path):
    if not path.exists():
        return [], "missing"
    body = path.read_bytes()
    truncated = bool(body and not body.endswith(b"\n"))
    if truncated:
        body = body.rpartition(b"\n")[0]
    return [json.loads(line) for line in body.splitlines()], (
        "incomplete_final_line" if truncated else "complete"
    )


def latency(values):
    ordered = sorted(values)
    if not ordered:
        return None
    if ordered[0] < 0:
        raise ValueError("negative operation duration")
    return {
        "samples": len(ordered),
        "mean_ms": statistics.mean(ordered) / 1e6,
        "min_ms": ordered[0] / 1e6,
        "max_ms": ordered[-1] / 1e6,
        **{
            name + "_ms": ordered[math.ceil(q * len(ordered)) - 1] / 1e6
            for name, q in (
                ("p50", 0.50),
                ("p95", 0.95),
                ("p99", 0.99),
                ("p999", 0.999),
            )
        },
        "method": "nearest rank over recorded request durations; small samples do not establish tail behavior",
    }


def compression_summary(root):
    measurements = {}
    for path in sorted(root.glob("verifications/*/verified.json")):
        proof = json.loads(path.read_text())
        reference = proof.get("storage_metrics")
        if reference is None:
            continue
        storage_path = path.parent / "storage-metrics.json"
        with storage_path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        storage = json.loads(storage_path.read_text())
        if (
            storage["passed"] is not True
            or reference["file"] != storage_path.name
            or reference["sha256"] != checksum
            or reference["snapshot"] != proof["snapshot"]
            or storage["snapshot"] != proof["snapshot"]
            or any(
                storage[key] != proof[key]
                for key in ("run_id", "verification_id", "config_sha256")
            )
        ):
            raise ValueError("unbound or cross-snapshot storage measurement")
        measurements[path.parent.name] = {
            "qualified_verification": proof["passed"] is True
            and proof["qualified"] is True,
            "snapshot": storage["snapshot"],
            "totals": storage["totals"],
            "encoded_parquet_chunk_compression_ratio": storage[
                "encoded_parquet_chunk_compression_ratio"
            ],
        }
    return {
        "measured": bool(measurements),
        "scope": "Sum of uncompressed/compressed Parquet column-chunk bytes for unique live files at one explicit committed snapshot. After encoding, including page headers; not CSV/raw application bytes, RSS or allocated disk. Verification qualification remains separate.",
        "codec_level": {
            "value": None,
            "reason": "Parquet footer records codec, not writer level; no explicit level override is used in this profile.",
        },
        "verifications": measurements,
        "unavailable_reason": None
        if measurements
        else "No completed, bound current-snapshot storage measurement; do not infer a ratio from CSV exports or allocated storage.",
    }


def operation_summary(case):
    case = Path(case)
    root = case / ("recovery-" + case.name)
    markers, marker_status = read_records(case / "phases.jsonl")
    windows = {
        left["phase"]: (right["host_ns"] - left["host_ns"]) / 1e9
        for left, right in zip(markers, markers[1:])
    }
    if any(duration <= 0 for duration in windows.values()):
        raise ValueError("invalid phase interval")
    files, phases, cells = {}, defaultdict(Counter), defaultdict(list)
    pending = []
    for path in (
        root / f"{role}-{number:03d}.jsonl"
        for role in ("writer", "reader")
        for number in range(2)
    ):
        records, files[path.name] = read_records(path)
        intents, outcomes = {}, set()
        for event in records:
            kind, started = event["kind"], event["phase"]
            if kind == "intent":
                intents[event["operation_id"]] = event
                phases[started]["write_intents_started"] += 1
                continue
            if kind not in {"outcome", "read"}:
                raise ValueError("unexpected live workload event kind")
            operation = "write" if kind == "outcome" else "read"
            if kind == "outcome":
                outcomes.add(event["operation_id"])
            ended = event.get("ended_phase", started)
            result = event["result"]
            engine = "duckdb" if operation == "write" else event["engine"]
            phases[ended][operation + "_completed_" + result] += 1
            if result == "ok":
                rows = (
                    event["end_id"] - event["first_id"]
                    if operation == "write"
                    else event["rows"]
                )
                phases[ended][operation + "_successful_rows"] += rows
            key = (operation, engine, started, ended, result, event.get("locality"))
            cells[key].append(event.get("duration_ns", 0))
        pending.extend(
            {
                "operation_id": identifier,
                "phase": event["phase"],
                "result": "ambiguous_no_outcome",
            }
            for identifier, event in intents.items()
            if identifier not in outcomes
        )
    summaries = {}
    for name, counts in sorted(phases.items()):
        seconds = windows.get(name)
        summaries[name] = {
            "host_phase_seconds": seconds,
            "counts": dict(sorted(counts.items())),
            "successful_request_rates_per_second": {
                op: counts[op + "_completed_ok"] / seconds for op in ("read", "write")
            }
            if seconds
            else None,
            "successful_row_rates_per_second": {
                op: counts[op + "_successful_rows"] / seconds
                for op in ("read", "write")
            }
            if seconds
            else None,
        }
    exports = {
        str(path.relative_to(case)): path.stat().st_size
        for path in sorted(root.glob("verifications/*/*-rows.csv"))
    }
    return {
        "scope": "Derived observations only, not a correctness verdict or capacity benchmark. Seed is separate. Counts use native start/end phase labels; rates use host phase durations. Crossing-phase requests retain both labels. Failed and successful latencies stay separate.",
        "journal_status": {"phases.jsonl": marker_status, **files},
        "pending_write_intents": pending,
        "read_attempt_boundary": "Read journals are written after completion; an interrupted in-flight read can be absent. Do not interpret recorded read counts as every attempted read.",
        "phases": summaries,
        "latencies": [
            dict(
                zip(
                    (
                        "operation",
                        "engine",
                        "started_phase",
                        "ended_phase",
                        "result",
                        "locality",
                    ),
                    key,
                )
            )
            | {"latency": latency(values)}
            for key, values in sorted(cells.items())
        ],
        "export_csv_bytes": exports,
        "compression": compression_summary(root),
    }
