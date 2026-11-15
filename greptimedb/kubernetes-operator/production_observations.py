"""Summarize completed operations against explicit fault/recovery phases."""

from datetime import datetime, timezone
import json
import math


def timestamp(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def summarize(writer_path, reader_path, markers, finished_at):
    if not markers or any(b[1] < a[1] for a, b in zip(markers, markers[1:])):
        raise ValueError("phase markers must be ordered")
    phases = {}
    for i, (name, started) in enumerate(markers):
        end = markers[i + 1][1] if i + 1 < len(markers) else finished_at
        phases[name] = {
            "seconds": (end - started).total_seconds(),
            "write_acked_requests": 0,
            "write_acked_rows": 0,
            "write_ambiguous_requests": 0,
            **{
                "read_" + outcome: 0
                for outcome in ("ok", "error", "missing", "stale", "wrong")
            },
            **{
                kind + "_latency": []
                for kind in (
                    "read_ok",
                    "read_unsuccessful",
                    "write_ack",
                    "write_ambiguous",
                )
            },
        }

    def phase(at):
        for name, started in reversed(markers):
            if at >= started:
                return phases[name]
        raise ValueError("operation completed before the experiment started")

    for line in writer_path.read_text().splitlines():
        event = json.loads(line)
        stage = event["stage"]
        if stage not in ("ack", "ambiguous"):
            continue
        target = phase(timestamp(event["at"]))
        if stage == "ack":
            target["write_acked_requests"] += 1
            target["write_acked_rows"] += event.get(
                "count", event.get("last", 0) - event.get("first", 0)
            )
        else:
            target["write_ambiguous_requests"] += 1
        if "latency_ms" in event:
            target["write_" + stage + "_latency"].append(event["latency_ms"])
    count = 0
    for line in reader_path.read_text().splitlines():
        event = json.loads(line)
        target = phase(timestamp(event["at"]))
        target["read_" + event["outcome"]] += 1
        target[
            "read_"
            + ("ok" if event["outcome"] == "ok" else "unsuccessful")
            + "_latency"
        ].append(event["latency_ms"])
        count += 1
    if not count:
        raise RuntimeError("reader produced no observations")
    failures = []
    for name, values in phases.items():
        for kind in ("read_ok", "read_unsuccessful", "write_ack", "write_ambiguous"):
            times = sorted(values.pop(kind + "_latency"))
            values[kind + "_samples"] = len(times)
            for percentile in (50, 99):
                values[f"{kind}_p{percentile}_ms"] = (
                    times[max(0, math.ceil(len(times) * percentile / 100) - 1)]
                    if times
                    else None
                )
        seconds = values["seconds"]
        attempts = values["write_acked_requests"] + values["write_ambiguous_requests"]
        values["write_completed_requests"] = attempts
        values["write_ack_fraction"] = (
            values["write_acked_requests"] / attempts if attempts else None
        )
        values["write_acked_rows_per_second"] = (
            values["write_acked_rows"] / seconds if seconds else None
        )
        values["read_ok_per_second"] = values["read_ok"] / seconds if seconds else None
        if values["read_wrong"]:
            failures.append(name + ": incorrect baseline values")
        if name in ("before", "after") and not values["read_ok"]:
            failures.append(name + ": no successful live reads")
        if name == "after" and (values["read_missing"] or values["read_stale"]):
            failures.append("after: baseline missing/stale after native recovery")
    return {
        "phase_assignment": "request completion timestamp; raw reads retain start times",
        "reader_scope": "preloaded immutable baseline, not read-after-write freshness",
        "markers": [{"phase": name, "at": at.isoformat()} for name, at in markers],
        "phases": phases,
        "contract_failures": failures,
    }
