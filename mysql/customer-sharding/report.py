"""Completion-time phases, separate success/error latency, and per-shard evidence."""

from datetime import datetime
import math


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def latency(events):
    values = sorted(e["latency_ms"] for e in events)
    return {
        "count": len(values),
        "p50_ms": values[max(0, math.ceil(len(values) * 0.5) - 1)] if values else None,
        "p99_ms": values[max(0, math.ceil(len(values) * 0.99) - 1)] if values else None,
        "max_ms": max(values) if values else None,
    }


def observations(case, markers, ended):
    import json

    writes = [
        json.loads(line)
        for line in (case / "writes.jsonl").read_text().split("\n")[:-1]
    ]
    reads = [
        json.loads(line) for line in (case / "reads.jsonl").read_text().split("\n")[:-1]
    ]
    phases = []
    for i, marker in enumerate(markers):
        start = timestamp(marker["at"])
        end = timestamp(markers[i + 1]["at"] if i + 1 < len(markers) else ended)
        shards = {}
        for shard in range(3):
            w = [
                e
                for e in writes
                if e["shard"] == shard and start <= timestamp(e["at"]) < end
            ]
            r = [
                e
                for e in reads
                if e["shard"] == shard and start <= timestamp(e["at"]) < end
            ]
            acks = [e for e in w if e["stage"] == "ack"]
            failures = [e for e in w if e["stage"] == "error"]
            ok = [e for e in r if e["outcome"] == "ok"]
            shards[str(shard)] = {
                "attempted_requests": sum(e["stage"] == "attempt" for e in w),
                "acknowledged_rows": sum(e["count"] for e in acks),
                "write_ok": latency(acks),
                "batch_completion_with_retries": latency(
                    [
                        {"latency_ms": e.get("batch_latency_ms", e["latency_ms"])}
                        for e in acks
                    ]
                ),
                "write_errors": latency(failures),
                "ambiguous_commit_responses": sum(
                    e.get("ambiguous", False) for e in failures
                ),
                "read_ok": latency(ok),
                "read_errors": latency([e for e in r if e["outcome"] != "ok"]),
                "wrong_or_missing_reads": sum(
                    e["outcome"] in ("wrong", "missing") for e in r
                ),
                "acked_rows_per_second": sum(e["count"] for e in acks) / (end - start)
                if end > start
                else None,
            }
        phases.append(
            {
                "phase": marker["phase"],
                "start": marker["at"],
                "seconds": end - start,
                "shards": shards,
            }
        )
    outages = {}
    for shard in range(3):
        shard_reads = [e for e in reads if e["shard"] == shard]
        successes = [timestamp(e["at"]) for e in shard_reads if e["outcome"] == "ok"]
        outages[str(shard)] = {
            "longest_gap_between_successful_read_completions_seconds": max(
                (b - a for a, b in zip(successes, successes[1:])), default=None
            ),
            "read_ok": sum(e["outcome"] == "ok" for e in shard_reads),
            "read_errors": sum(e["outcome"] != "ok" for e in shard_reads),
        }
    return {
        "reader_scope": "immutable prior ACK bodies, or seeded customers when no earlier orders exist; not fresh-write visibility lag",
        "reader_interval_ms": 100,
        "reader_timeout_seconds": 3,
        "phase_assignment": "completion timestamp; raw events also retain start time",
        "phases": phases,
        "shard_reads": outages,
        "contract_failures": sum(e["outcome"] in ("missing", "wrong") for e in reads),
    }
