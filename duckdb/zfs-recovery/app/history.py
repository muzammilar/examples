"""External, append-only evidence and the independent event-body oracle."""

import csv
import hashlib
import json
import math
import os
import re
from pathlib import Path
import statistics
import time


def verify_arc(memory, effective, expected):
    match = re.search(r"^c_max\s+\d+\s+(\d+)\s*$", memory, re.M)
    if match is None or int(match.group(1)) != expected or effective != expected:
        raise RuntimeError("effective ZFS ARC maximum differs from the declared256MiB")
    return int(match.group(1))


def witness_hashes(root):
    root = Path(root)
    paths = [
        *root.glob("*.jsonl"),
        *root.glob("*-errors.json"),
        *root.glob("live/*/writer-*.jsonl"),
        *root.glob("live/*/reader.jsonl"),
        *root.glob("live/*/config.json"),
        *root.glob("live/*/generation.json"),
    ]
    output = {}
    for path in sorted(paths):
        with path.open("rb") as stream:
            output[str(path.relative_to(root))] = hashlib.file_digest(
                stream, "sha256"
            ).hexdigest()
    return output


def reconcile_legacy(
    csv_path, root, labels, customers, missing_labels=(), error_labels=()
):
    acknowledged, possible = {}, {}
    for label in labels:
        for path in Path(root).glob(label + "-*.jsonl"):
            for event in events(path):
                key = (event["first_id"], event["end_id"])
                if key in acknowledged:
                    raise RuntimeError("duplicate legacy acknowledgement")
                acknowledged[key] = label
    for label in error_labels:
        for path in Path(root).glob(label + "-*-errors.json"):
            for event in json.loads(path.read_text()):
                if event["outcome"] == "ambiguous":
                    possible[(event["first_id"], event["end_id"])] = label
    counts = {key: 0 for key in acknowledged | possible}
    previous, rows = -1, 0
    with Path(csv_path).open(newline="") as stream:
        reader = csv.reader(stream)
        if next(reader) != [
            "event_id",
            "customer_id",
            "amount_cents",
            "batch_start",
            "batch_end",
        ]:
            raise RuntimeError("unexpected legacy export columns")
        for row in reader:
            identity, customer, amount, first, end = map(int, row)
            if (
                identity <= previous
                or (first, end) not in counts
                or not first <= identity < end
                or customer != identity % customers
                or amount != identity % 10000 + 1
            ):
                raise RuntimeError("wrong, duplicated or unexpected legacy body")
            previous, rows = identity, rows + 1
            counts[(first, end)] += 1
    missing = {}
    for key, count in counts.items():
        required = key[1] - key[0]
        if count not in (0, required):
            raise RuntimeError("partial legacy batch")
        label = acknowledged.get(key)
        if label is not None and count != required:
            missing[label] = missing.get(label, 0) + required
            if label not in missing_labels:
                raise RuntimeError("legacy acknowledged rows missing")
    with Path(csv_path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "rows_verified": rows,
        "acknowledged_rows": sum(hi - lo for lo, hi in acknowledged),
        "missing_acknowledged_by_label": missing,
        "applied_unacknowledged_batches": sum(
            bool(counts[key]) for key in possible if key not in acknowledged
        ),
        "wrong_duplicate_or_partial_rows": 0,
        "csv_sha256": digest,
        "csv_bytes": Path(csv_path).stat().st_size,
    }


def save(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def emit(stream, value):
    stream.write(json.dumps(value, separators=(",", ":")) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def events(path, complete=True):
    content = Path(path).read_bytes()
    if content and not content.endswith(b"\n"):
        if complete:
            raise RuntimeError("incomplete history: " + str(path))
        content = content.rpartition(b"\n")[0]
    return [json.loads(line) for line in content.splitlines()]


def body(identity, first, end, customers):
    return [
        identity,
        identity % customers,
        identity % 10000 + 1,
        hashlib.sha256(str(identity).encode()).hexdigest(),
        first,
        end,
    ]


def write_intents(root, config):
    intents, acknowledged = {}, set()
    paths = sorted(Path(root).glob("writer-*.jsonl"))
    if {path.name for path in paths} != {
        f"writer-{i}.jsonl" for i in range(config["clients"])
    }:
        raise RuntimeError("missing or unexpected writer history")
    for path in paths:
        pending = {}
        seq = 0
        for event in events(path):
            key = event["first"]
            if event["kind"] == "intent":
                seq += 1
                expected_first = (
                    config["rows"]
                    + ((seq - 1) * config["clients"] + event["worker"])
                    * config["batch_rows"]
                )
                if (
                    key in intents
                    or key != expected_first
                    or event["end"] != key + config["batch_rows"]
                    or event["seq"] != seq
                    or path.name != f"writer-{event['worker']}.jsonl"
                ):
                    raise RuntimeError("duplicate/invalid mutation intent")
                intents[key] = event
                pending[key] = event
            else:
                if key not in pending or event["end"] != pending.pop(key)["end"]:
                    raise RuntimeError("outcome without its matching intent")
                intents[key]["outcome"] = event
                if event["kind"] == "ok":
                    acknowledged.add(key)
                elif event["kind"] not in ("fail", "ambiguous"):
                    raise RuntimeError("unknown writer outcome")
        if pending:
            raise RuntimeError("writer stopped without recording every intent outcome")
    return intents, acknowledged


def reconcile(csv_path, root, config):
    intents, acknowledged = write_intents(root, config)
    counts = {first: 0 for first in intents}
    seed = config["rows"]
    previous, rows, seed_count = -1, 0, 0
    with Path(csv_path).open(newline="") as stream:
        reader = csv.reader(stream)
        if next(reader) != [
            "event_id",
            "customer_id",
            "amount_cents",
            "payload",
            "batch_start",
            "batch_end",
        ]:
            raise RuntimeError("unexpected export columns")
        for value in reader:
            identity, customer, amount, payload, first, end = value
            actual = [
                int(identity),
                int(customer),
                int(amount),
                payload,
                int(first),
                int(end),
            ]
            identity, first, end = actual[0], actual[4], actual[5]
            if identity <= previous:
                raise RuntimeError("duplicate or unordered event")
            previous, rows = identity, rows + 1
            if identity < seed:
                if identity != rows - 1 or (first, end) != (0, seed):
                    raise RuntimeError("seed body missing or assigned to wrong batch")
                seed_count += 1
            elif (
                first not in intents
                or intents[first]["end"] != end
                or not first <= identity < end
            ):
                raise RuntimeError("unexpected event outside witnessed intents")
            else:
                counts[first] += 1
            if actual != body(identity, first, end, config["customers"]):
                raise RuntimeError("wrong event body: " + str(identity))
    if seed_count != seed:
        raise RuntimeError("seed rows missing")
    for first, intent in intents.items():
        expected = intent["end"] - first
        if counts[first] not in (0, expected):
            raise RuntimeError("partially committed batch")
        if first in acknowledged and counts[first] != expected:
            raise RuntimeError("acknowledged batch missing")
        if intent["outcome"]["kind"] == "fail" and counts[first]:
            raise RuntimeError("known pre-submission failure produced rows")
    reads = events(Path(root) / "reader.jsonl")
    watermark_before = 0
    for event in reads:
        if event["kind"] == "mismatch":
            raise RuntimeError("reader observed wrong acknowledged data")
        if event["kind"] == "ok":
            expected = [body(i, 0, seed, config["customers"]) for i in (0, seed - 1)]
            eligible = [
                intents[first]["outcome"]
                for first in acknowledged
                if intents[first]["outcome"]["ended_ns"] < event["started_ns"]
            ]
            watermark = event["acknowledged_ended_ns"]
            if (watermark or 0) < watermark_before:
                raise RuntimeError("reader acknowledgement watermark moved backward")
            watermark_before = watermark or 0
            if watermark is not None:
                witnessed = [e for e in eligible if e["ended_ns"] == watermark]
                if len(witnessed) != 1:
                    raise RuntimeError("read targets an unwitnessed acknowledgement")
                ack = witnessed[0]
                expected += [
                    body(i, ack["first"], ack["end"], config["customers"])
                    for i in (ack["first"], ack["end"] - 1)
                ]
            if event["actual"] != sorted(expected) or event["expected"] != sorted(
                expected
            ):
                raise RuntimeError(
                    "reader history bodies differ from independent oracle"
                )
    with Path(csv_path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "rows_verified": rows,
        "seed_rows_verified": seed,
        "mutation_intents": len(intents),
        "acknowledged_batches": len(acknowledged),
        "acknowledged_rows": sum(intents[k]["end"] - k for k in acknowledged),
        "applied_unacknowledged_batches": sum(
            bool(counts[k]) for k in counts if k not in acknowledged
        ),
        "acknowledged_missing": 0,
        "wrong_duplicate_partial_or_unexpected_rows": 0,
        "successful_reads_verified": sum(e["kind"] == "ok" for e in reads),
        "csv_bytes": Path(csv_path).stat().st_size,
        "csv_sha256": digest,
    }


def summaries(root):
    phases = events(Path(root) / "phases.jsonl")
    output = {}
    for index, phase in enumerate(phases):
        start = phase["monotonic_ns"]
        end = (
            phases[index + 1]["monotonic_ns"]
            if index + 1 < len(phases)
            else time.monotonic_ns()
        )
        values = {}
        for role, pattern in (("writer", "writer-*.jsonl"), ("reader", "reader.jsonl")):
            selected = [
                event
                for path in Path(root).glob(pattern)
                for event in events(path)
                if event["kind"] != "intent" and start <= event["started_ns"] < end
            ]
            values[role] = {}
            for kind in ("ok", "fail", "ambiguous", "mismatch"):
                durations = sorted(
                    e["elapsed_ns"] / 1e6 for e in selected if e["kind"] == kind
                )
                values[role][kind] = {
                    "count": len(durations),
                    "rate_per_second": len(durations) * 1e9 / (end - start),
                    "p50_ms": statistics.median(durations) if durations else None,
                    "p99_ms": durations[math.ceil(len(durations) * 0.99) - 1]
                    if durations
                    else None,
                }
        output[phase["name"]] = {"seconds": (end - start) / 1e9, "operations": values}
    return output


class Acknowledgements:
    def __init__(self, root):
        self.root, self.offsets, self.latest = root, {}, None

    def poll(self):
        for path in self.root.glob("writer-*.jsonl"):
            offset = self.offsets.get(path, 0)
            with path.open("rb") as stream:
                if path.stat().st_size < offset:
                    raise RuntimeError("live witness was truncated")
                stream.seek(offset)
                data = stream.read()
            end = data.rfind(b"\n")
            if end < 0:
                continue
            self.offsets[path] = offset + end + 1
            for line in data[: end + 1].splitlines():
                event = json.loads(line)
                if event["kind"] == "ok" and (
                    self.latest is None or event["ended_ns"] > self.latest["ended_ns"]
                ):
                    self.latest = event
        return self.latest
