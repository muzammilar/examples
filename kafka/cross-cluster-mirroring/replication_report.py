"""Bounded-memory complete live-delivery reconciliation and phase histograms."""

from array import array
from bisect import bisect_right
from collections import Counter
import gzip
import json
import math


def events(path):
    with gzip.open(path, "rt") as stream:
        for line in stream:
            yield json.loads(line)


class Histogram:
    def __init__(self):
        self.bins = Counter()
        self.count = 0
        self.first = None
        self.last = None
        self.longest_gap = 0

    def add(self, seconds, timestamp):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("invalid measured latency")
        self.bins[math.ceil(seconds * 1_000_000)] += 1
        self.count += 1
        if self.first is None:
            self.first = timestamp
        if self.last is not None:
            self.longest_gap = max(self.longest_gap, timestamp - self.last)
        self.last = timestamp

    def summary(self, duration):
        ordered = sorted(self.bins.items())

        def percentile(fraction):
            threshold = math.ceil(self.count * fraction)
            accumulated = 0
            for bucket, count in ordered:
                accumulated += count
                if accumulated >= threshold:
                    return bucket / 1000
            return None

        return {
            "count": self.count,
            "rate_per_second": self.count / duration if duration else None,
            "p50_ms": percentile(0.5),
            "p99_ms": percentile(0.99),
            "longest_completion_gap_seconds": self.longest_gap,
            "latency_resolution_microseconds": 1,
        }


def reconcile(root, plan, phases):
    maximum = plan["minimum_messages"] * 4
    hashes = bytearray(maximum * 32)
    state = bytearray(maximum)
    seen = bytearray(maximum)
    partition = array("b", [-1]) * maximum
    offset = array("q", [-1]) * maximum
    boundaries = [p["time"] for p in phases]
    measures = {}
    overall = {}
    by_partition = {}
    fault_first = {
        str(i): {"marker": marker, "write_ack": None, "read_message_poll": None}
        for i, marker in enumerate(phases)
        if marker["phase"] == "fault"
    }

    def measure(kind, event, seconds=None):
        index = bisect_right(boundaries, event["time"]) - 1
        index = max(0, index)
        histogram = measures.setdefault((index, kind), Histogram())
        histogram.add(event["seconds"] if seconds is None else seconds, event["time"])
        overall.setdefault(kind, Histogram()).add(
            event["seconds"] if seconds is None else seconds, event["time"]
        )
        if isinstance(event.get("partition"), int) and event["partition"] >= 0:
            by_partition.setdefault((event["partition"], kind), Histogram()).add(
                event["seconds"] if seconds is None else seconds, event["time"]
            )
        for fault in fault_first.values():
            if (
                kind in ("write_ack", "read_message_poll")
                and event["time"] >= fault["marker"]["time"]
                and fault[kind] is None
            ):
                fault[kind] = {
                    "completion_time": event["time"],
                    "seconds_after_marker": event["time"] - fault["marker"]["time"],
                }

    intended = acknowledged = ambiguous = rejected = unknown_ack_offset = 0
    for e in events(root / "producer.jsonl.gz"):
        seq = e["seq"]
        if not 0 <= seq < maximum or e["id"] != f"{plan['run_id']}-order-{seq}":
            raise ValueError("foreign or unbounded intended ID")
        digest = bytes.fromhex(e["sha256"])
        if len(digest) != 32:
            raise ValueError("invalid expected digest")
        if e["stage"] == "intent":
            if state[seq]:
                raise ValueError("duplicate intent")
            state[seq] = 1
            intended += 1
            hashes[seq * 32 : (seq + 1) * 32] = digest
        else:
            if state[seq] != 1 or hashes[seq * 32 : (seq + 1) * 32] != digest:
                raise ValueError("orphan, duplicate or changed producer outcome")
            stage = e["stage"]
            if stage == "ack":
                state[seq] = 2
                partition[seq] = e["partition"]
                if e["offset"] is None:
                    if (
                        e.get("offset_known") is not False
                        or e.get("persistence_status") != 2
                    ):
                        raise ValueError(
                            "unknown ACK offset lacks native persisted status"
                        )
                    unknown_ack_offset += 1
                else:
                    if e["offset"] < 0:
                        raise ValueError("invalid known ACK offset")
                    offset[seq] = e["offset"]
                acknowledged += 1
            elif stage == "ambiguous":
                state[seq] = 3
                ambiguous += 1
            elif stage == "rejected":
                state[seq] = 4
                rejected += 1
            else:
                raise ValueError("unknown producer outcome")
            measure("write_" + stage, e)
    if any(not s for s in state[:intended]):
        raise ValueError("gap in intended IDs")
    duplicates = nonack = observed_ack = 0
    last = {}
    for e in events(root / "reader.jsonl.gz"):
        if e["stage"] != "message":
            if e["stage"] == "wrong":
                raise ValueError("live reader detected corrupt data")
            if e["stage"] in ("poll_error", "watermark", "watermark_error"):
                measure("read_" + e["stage"], e)
            continue
        seq = e["seq"]
        if (
            not 0 <= seq < maximum
            or state[seq] in (0, 4)
            or e["id"] != f"{plan['run_id']}-order-{seq}"
            or hashes[seq * 32 : (seq + 1) * 32] != bytes.fromhex(e["sha256"])
        ):
            raise ValueError("unexpected or corrupt live message")
        position = (e["partition"], e["offset"])
        if state[seq] == 2 and (
            position[0] != partition[seq]
            or (
                not plan["mirrored"] and offset[seq] >= 0 and position[1] != offset[seq]
            )
        ):
            raise ValueError("live delivery differs from acknowledged position")
        if seen[seq]:
            if position != (partition[seq], offset[seq]):
                raise ValueError("duplicate logical message at a different offset")
            duplicates += 1
        else:
            prior = last.get(e["partition"])
            if prior and (e["offset"] <= prior[0] or seq <= prior[1]):
                raise ValueError("live per-partition ordering violation")
            last[e["partition"]] = (e["offset"], seq)
            seen[seq] = 1
            partition[seq], offset[seq] = position
            if state[seq] == 2:
                observed_ack += 1
            else:
                nonack += 1
        measure("read_message_poll", e)
        measure(
            "read_message_end_to_end", e, max(0, e["time"] - e["created_ms"] / 1000)
        )
    missing = sum(s == 2 and not seen[i] for i, s in enumerate(state))
    if missing:
        raise ValueError(f"{missing} acknowledged messages missing from live witness")
    summaries = {}
    for (index, kind), histogram in measures.items():
        phase = phases[index]["phase"]
        if sum(p["phase"] == phase for p in phases) > 1:
            phase = str(index) + ":" + phase
        end = (
            phases[index + 1]["time"]
            if index + 1 < len(phases)
            else phases[index]["time"]
        )
        summaries.setdefault(phase, {})[kind] = histogram.summary(
            end - phases[index]["time"]
        )
    return {
        "intended": intended,
        "acknowledged": acknowledged,
        "acknowledged_without_native_offset": unknown_ack_offset,
        "ambiguous": ambiguous + state.count(1),
        "rejected": rejected,
        "live_observed_acknowledged": observed_ack,
        "live_visible_nonack": nonack,
        "duplicate_live_deliveries": duplicates,
        "acknowledged_loss": missing,
        "corruption": 0,
        "phases": summaries,
        "overall": {
            kind: histogram.summary((histogram.last or 0) - (histogram.first or 0))
            for kind, histogram in overall.items()
        },
        "partition_observations": {
            str(partition) + ":" + kind: histogram.summary(
                (histogram.last or 0) - (histogram.first or 0)
            )
            for (partition, kind), histogram in by_partition.items()
        },
        "first_success_after_fault": fault_first,
        "completion_gap_boundary": "Longest interval between observed completions including phase boundaries; not proof of complete service outage. First success may have started before injection.",
        "latency_boundaries": {
            "write": "producer admission through delivery callback; includes native retries",
            "read_message_poll": "one native consumer poll, which may serve prefetched data",
            "read_message_end_to_end": "producer wall-clock serialization to live reader observation",
            "read_watermark": "native ListOffsets RPC on partition 0; not a message body read",
        },
    }
