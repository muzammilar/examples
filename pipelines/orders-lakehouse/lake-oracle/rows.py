"""Source-ledger and exact-row checks, independent of any engine API."""

from array import array
import json
from pathlib import Path


def witness(phase, total):
    offsets = array("q", [-1]) * (total + 1)
    partitions = bytearray(total + 1)
    count = 0
    with (Path("/witness") / (phase + ".jsonl")).open() as stream:
        for line in stream:
            row = json.loads(line)
            if row["phase"] != phase:
                raise ValueError("Wrong witness phase")
            if row["kind"] != "result":
                continue
            if row["unknown"] or len(row["acks"]) != row["count"]:
                raise ValueError(
                    "Ambiguous source writes require separate reconciliation"
                )
            for ack in row["acks"]:
                key = ack["id"]
                if not 1 <= key <= total or offsets[key] != -1 or ack["offset"] < 0:
                    raise ValueError("Duplicate/out-of-range ACK")
                if ack["partition"] not in (0, 1, 2):
                    raise ValueError("Wrong Kafka partition")
                offsets[key], partitions[key] = ack["offset"], ack["partition"]
                count += 1
    if count != total:
        raise ValueError(f"Incomplete closed {phase} witness: {count}/{total}")
    return offsets, partitions, bytearray(total + 1)


def check_row(row, partition, journals, customers):
    phase, key = row[:2]
    if phase not in journals or type(key) is not int:
        raise ValueError("Unexpected phase/key")
    offsets, parts, seen = journals[phase]
    if not 1 <= key < len(seen) or seen[key] or offsets[key] < 0:
        raise ValueError(f"Duplicate/unacknowledged key: {phase}/{key}")
    customer = 1 + (key - 1) % customers
    if parts[key] != customer % 3 or any(
        type(row[i]) is not int for i in (1, 2, 3, 5, 6, 7, 8, 9, 10)
    ):
        raise ValueError("Wrong Kafka routing or numeric type")
    expected = (
        phase,
        key,
        customer,
        100 + key % 1000,
        f"region-{customer % 16}",
        1,
        1,
        1700000000 + key % 86400,
        parts[key],
        offsets[key],
        customer % 3,
    )
    if row != expected or tuple(partition) != (phase, customer % 3):
        raise ValueError(f"Wrong body/partition: {phase}/{key}: {row!r}")
    seen[key] = 1
