"""Per-phase API and visibility measurements from preserved raw traffic journals."""

import math
from itertools import pairwise
from pathlib import Path

from version_history import records


def distribution(events):
    result = {"count": len(events)}
    if events:
        samples = sorted(event["seconds"] * 1000 for event in events)
        result.update(
            {
                f"p{p}_ms": samples[math.ceil(len(samples) * p / 100) - 1]
                for p in [50, 99]
            }
        )
        result["max_ms"] = samples[-1]
    return result


def summarize(directory):
    directory = Path(directory)
    writes = records(directory / "writer.jsonl")
    reads = records(directory / "reader.jsonl")
    marks = records(directory / "phases.jsonl")
    # A failed run may never emit its next phase; retain observations through
    # its last complete durable event, without inventing a native repair time.
    last_observation = max((e["time"] for e in writes + reads), default=0)
    if (
        marks
        and marks[-1]["phase"] not in {"verified", "aborted"}
        and last_observation > marks[-1]["time"]
    ):
        marks.append(
            {
                "phase": "last_durable_observation",
                "time": math.nextafter(last_observation, math.inf),
            }
        )
    requests = {}
    for event in writes:
        if event["stage"] in {"ack", "ambiguous", "rejected"}:
            key = event.get("request_id") or (
                event["rows"][0]["version"],
                event["server"],
            )
            requests.setdefault(key, event | {"outcomes": set()})["outcomes"].add(
                event["stage"]
            )
    phases = {}
    for start, end in pairwise(marks):
        seconds = end["time"] - start["time"]
        phase = {"seconds": seconds}
        for stage in ["ack", "ambiguous", "rejected"]:
            selected = [
                e
                for e in writes
                if e["stage"] == stage and start["time"] <= e["time"] < end["time"]
            ]
            rows = sum(len(e["rows"]) for e in selected)
            phase[stage] = {"rows": rows, "rows_per_second": rows / seconds}
        selected = [
            e for e in requests.values() if start["time"] <= e["time"] < end["time"]
        ]
        phase["write_requests"] = distribution(selected)
        phase["successful_write_requests"] = distribution(
            [e for e in selected if e["outcomes"] == {"ack"}]
        )
        phase["unresolved_or_rejected_write_requests"] = distribution(
            [e for e in selected if e["outcomes"] != {"ack"}]
        )
        for stage in [
            "search",
            "search_error",
            "get",
            "get_error",
            "stale",
        ]:
            selected = [
                e
                for e in reads
                if e["stage"] == stage and start["time"] <= e["time"] < end["time"]
            ]
            phase[stage] = distribution(selected) | {
                "operations_per_second": len(selected) / seconds
            }
        for diagnostic in ["stale_search_hit", "partial_search_hit"]:
            count = sum(
                e["stage"] == diagnostic and start["time"] <= e["time"] < end["time"]
                for e in reads
            )
            phase[diagnostic] = {
                "count": count,
                "observations_per_second": count / seconds,
            }
        observed_reads = [e for e in reads if start["time"] <= e["time"] < end["time"]]
        phase["additional_read_attempts"] = sum(
            max(0, e.get("attempts", 1) - 1) for e in observed_reads
        )
        phase["successful_operations_with_failed_attempts"] = sum(
            e["stage"] in {"search", "get", "stale"} and bool(e.get("error"))
            for e in observed_reads
        )
        phase["stale_ack_age"] = distribution(
            [
                {"seconds": e["ack_age_seconds"]}
                for e in observed_reads
                if e["stage"] == "stale"
            ]
        )
        phases[start["phase"]] = phase
    gaps = {}
    for stage in ["search", "get", "ack"]:
        source = writes if stage == "ack" else reads
        times = ([marks[0]["time"]] if marks else []) + [
            event["time"] for event in source if event["stage"] == stage
        ]
        if marks and times:
            end = max((event["time"] for event in source), default=marks[0]["time"])
            if stage == "ack":
                end = next(
                    (mark["time"] for mark in marks if mark["phase"] == "reconcile"),
                    end,
                )
            times.append(end)
        gaps[stage] = max((b - a for a, b in pairwise(times)), default=0)
    totals = {
        stage: sum(len(e["rows"]) for e in writes if e["stage"] == stage)
        for stage in ["attempt", "ack", "ambiguous", "rejected"]
    }
    return {
        "phases": phases,
        "writer_rows": totals,
        "pending_outcome_rows": totals["attempt"]
        - totals["ack"]
        - totals["ambiguous"]
        - totals["rejected"],
        "completion_gap_scope": "from baseline to writer-stop reconciliation marker (ACK) or last durable role observation; intentional post-writer-stop reconciliation excluded",
        "read_operations": {
            stage: sum(e["stage"] == stage for e in reads)
            for stage in [
                "search",
                "search_error",
                "get",
                "get_error",
                "stale",
                "wrong",
                "stale_search_hit",
                "partial_search_hit",
            ]
        },
        "longest_successful_completion_gap_seconds": gaps,
        "write_request_latency_samples": len(requests),
        "scope": "rate-limited fault observations; API latency excludes local journal fsync; not a capacity benchmark",
    }
