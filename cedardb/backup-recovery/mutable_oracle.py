"""Independent replay of transactional intents/receipts and complete mutable bodies."""

import csv
import bisect
import hashlib
import json

import control


def pair(index):
    left = index // 100 * 200 + index % 100
    return left, left + 100


def body(identity, revision=0, right=False):
    customer, amount, payload = (
        identity % 10000,
        (identity % 100000) * 17 % 100000,
        control.body(identity),
    )
    if revision:
        delta = 1 + revision % 997
        amount += -delta if right else delta
        payload = (
            payload[:192]
            + hashlib.sha256(f"mutable:{identity}:{revision}".encode()).hexdigest()
        )
    return {
        "id": identity,
        "customer": customer,
        "amount": amount,
        "payload": payload,
        "revision": revision,
    }


def operation(seq):
    return "paired-update" if seq % 2 else "delete-reinsert"


def reconcile(rows_path, receipts_path, journal_path, count, pairs, reader_path=None):
    events = [json.loads(line) for line in journal_path.read_text().splitlines()]
    intents, outcomes = {}, {}
    for event in events:
        seq = event["seq"]
        if event["kind"] == "intent":
            if seq <= 0 or seq in intents:
                raise RuntimeError("duplicate/invalid mutation intent")
            ids = pair((seq - 1) // 2 % pairs)
            expected = [body(ids[0], seq), body(ids[1], seq, True)]
            if event["rows"] != expected or event["operation"] != operation(seq):
                raise RuntimeError("mutation intent differs from independent oracle")
            intents[seq] = event
        elif event["kind"] in {"ok", "fail", "ambiguous", "mismatch"}:
            if seq not in intents or seq in outcomes:
                raise RuntimeError("outcome lacks a unique preceding intent")
            if event["kind"] == "mismatch":
                raise RuntimeError("native application reported mismatch")
            outcomes[seq] = event
        else:
            raise RuntimeError("unknown mutation witness record")
    committed, latest = set(), {}
    with receipts_path.open() as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["seq", "operation", "left_id", "right_id"]:
            raise RuntimeError("mutation receipt schema differs")
        previous = 0
        for receipt in reader:
            seq = int(receipt["seq"])
            if seq <= previous or seq not in intents:
                raise RuntimeError(
                    "native receipt duplicates/reorders or invents an intent"
                )
            ids = pair((seq - 1) // 2 % pairs)
            if (int(receipt["left_id"]), int(receipt["right_id"])) != ids or receipt[
                "operation"
            ] != operation(seq):
                raise RuntimeError("committed receipt has wrong operation/pair")
            committed.add(seq)
            for index, identity in enumerate(ids):
                latest[identity] = body(identity, seq, bool(index))
            previous = seq
    acknowledged = {seq for seq, event in outcomes.items() if event["kind"] == "ok"}
    if acknowledged - committed:
        raise RuntimeError("acknowledged mutation receipt missing")
    groups = [[0, 0, 0] for _ in range(100)]
    expected_groups = [[0, 0, 0] for _ in range(100)]
    with rows_path.open() as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["id", "customer", "amount", "payload", "revision"]:
            raise RuntimeError("mutable row schema differs")
        observed = 0
        for identity in range(count):
            raw = next(reader, None)
            if raw is None:
                raise RuntimeError("mutable export missing seed identity")
            actual = {
                key: value if key == "payload" else int(value)
                for key, value in raw.items()
            }
            initial = body(identity)
            if actual != latest.get(identity, initial):
                raise RuntimeError(
                    "wrong/missing/duplicate/partial mutable body: " + str(identity)
                )
            bucket = groups[actual["customer"] % 100]
            bucket[0] += 1
            bucket[1] += actual["amount"]
            bucket[2] += len(actual["payload"].encode())
            bucket = expected_groups[initial["customer"] % 100]
            bucket[0] += 1
            bucket[1] += initial["amount"]
            bucket[2] += len(initial["payload"].encode())
            observed += 1
        if next(reader, None) is not None:
            raise RuntimeError("mutable export has unexpected rows")
    if groups != expected_groups:
        raise RuntimeError("committed mutations broke analytical bucket invariants")
    hashes = {}
    for label, path in (("row_export", rows_path), ("receipt_export", receipts_path)):
        with path.open("rb") as stream:
            hashes[label + "_sha256"] = hashlib.file_digest(
                stream, "sha256"
            ).hexdigest()
    read_proof = None
    if reader_path is not None:
        read_proof = verify_reads(reader_path, events, committed)
    return {
        "seed_rows_verified": observed,
        "intents": len(intents),
        "acknowledged_transactions": len(acknowledged),
        "committed_transactions": len(committed),
        "applied_unacknowledged_intents": len(committed - acknowledged),
        "acknowledged_loss": 0,
        "wrong_duplicate_or_partial_rows": 0,
        "groups_verified": 100,
        "last_committed_seq": max(committed, default=0),
        **hashes,
        "read_history": read_proof,
    }


def verify_reads(path, writes, committed):
    readers = [json.loads(line) for line in path.read_text().splitlines()]
    acknowledgements = sorted(
        (e["ended_ns"], e["seq"]) for e in writes if e["kind"] == "ok"
    )
    times, lower_bounds, latest = [], [], 0
    for timestamp, seq in acknowledgements:
        times.append(timestamp)
        latest = max(latest, seq)
        lower_bounds.append(latest)
    successes, previous_request, previous_seq = 0, 0, 0
    for event in readers:
        if event["request"] != previous_request + 1:
            raise RuntimeError("reader request IDs skipped/reused")
        previous_request = event["request"]
        if event["kind"] == "mismatch":
            raise RuntimeError("reader recorded a snapshot mismatch")
        if event["kind"] != "ok":
            continue
        seq = event["seq"]
        if seq < previous_seq or (seq != 0 and seq not in committed):
            raise RuntimeError("reader observed regressed/nonexistent commit watermark")
        # Both Go processes run in the same Linux VM. The guard keeps boundary
        # clock/recording noise out of this lower-bound observation.
        index = bisect.bisect_right(times, event["started_ns"] - 10_000_000) - 1
        if index >= 0 and seq < lower_bounds[index]:
            raise RuntimeError("snapshot predates an earlier acknowledged commit")
        previous_seq, successes = seq, successes + 1
    return {
        "successful_snapshots": successes,
        "requests": len(readers),
        "mismatches": 0,
        "ack_lower_bound_clock_guard_ms": 10,
        "scope": "Four-body repeatable-read snapshots and prior-ACK watermark lower bounds; shared-VM UTC with a conservative guard, not a general linearizability proof",
    }
