"""Exact body/witness reconciliation and native Kafka fences."""

import hashlib
import json
from lab import ROOT, app, fixture, pg, sql, kafka, wait, save


def reconcile(path):
    summaries = app("verify", path)
    bodies = [r for r in summaries if r.get("event") == "verified"]
    if len(bodies) != 3 or len({r["body_sha256"] for r in bodies}) != 1:
        raise RuntimeError("Incomplete fixed-replica body checks")
    cases = []
    source_receipts = json.loads(
        pg(
            "SELECT coalesce(json_agg(json_build_array(o.id,o.customer_id,o.amount_cents,o.event_second,r.id)), '[]') "
            "FROM receipts r JOIN orders o ON o.id=r.first_id WHERE r.operation='live'"
        )
    )
    receipt_bodies = {row[4]: row for row in source_receipts}
    if len(receipt_bodies) != len(source_receipts):
        raise RuntimeError("Duplicate source receipts")
    seen_receipts = set()
    for journal in sorted((ROOT / "results/cases").glob("*/witness.jsonl")):
        metadata = json.loads((journal.parent / "case.json").read_text())
        if metadata["fixture"] != fixture()["id"]:
            continue
        attempts = {}
        acked = []
        for row in map(json.loads, journal.read_text().splitlines()):
            seq = row["sequence"]
            if row["event"] == "intent":
                if seq in attempts:
                    raise RuntimeError("Repeated intent")
                attempts[seq] = row
            elif row["event"] == "ack":
                if seq not in attempts or row["receipt"] in seen_receipts:
                    raise RuntimeError("Invalid ACK history")
                if any(
                    row[k] != attempts[seq][k]
                    for k in [
                        "receipt",
                        "id",
                        "customer_id",
                        "amount_cents",
                        "event_second",
                        "sha256",
                    ]
                ):
                    raise RuntimeError("ACK differs from intent")
                seen_receipts.add(row["receipt"])
                acked.append(row)
            else:
                raise RuntimeError("Ambiguous source write needs diagnosis")
        if len(attempts) != len(acked):
            raise RuntimeError("Incomplete source witness")
        for row in acked:
            expected_hash = hashlib.sha256(
                f"{row['id']}:{row['customer_id']}:{row['amount_cents']}:{row['event_second']}".encode()
            ).hexdigest()
            if expected_hash != row["sha256"]:
                raise RuntimeError("Witness checksum differs from declared body")
            receipt = row["receipt"]
            if len(receipt) > 64 or any(c not in "0123456789abcdef:" for c in receipt):
                raise RuntimeError("Invalid receipt identity")
            observed = receipt_bodies.get(receipt)
            if observed != [
                row["id"],
                row["customer_id"],
                row["amount_cents"],
                row["event_second"],
                receipt,
            ]:
                raise RuntimeError("Acknowledged insert/receipt missing or changed")
        cases.append({"journal": str(journal.relative_to(ROOT)), "acks": len(acked)})
    if not cases:
        raise RuntimeError("No external source witness")
    save(
        path / "reconciled.json",
        {
            "replicas": bodies,
            "histories": cases,
            "total_acks": sum(c["acks"] for c in cases),
            "wrong_or_missing": 0,
        },
    )
    return cases


def offset_fence(path):
    expected = {}
    for topic in ["cdc.public.customers", "cdc.public.orders"]:
        raw = kafka(
            "/opt/kafka/bin/kafka-get-offsets.sh",
            "--bootstrap-server",
            "broker1:9092",
            "--topic",
            topic,
            "--time",
            "-1",
        )
        (path / (topic + "-ends.txt")).write_text(raw)
        for line in raw.splitlines():
            name, part, end = line.split(":")
            expected[(name, int(part))] = int(end)

    def caught_up():
        rows = sql(
            "SELECT topic,partition,uniqExact(offset) AS positions,max(offset)+1 AS end FROM pipeline.cdc_history GROUP BY topic,partition"
        )
        actual = {
            (r["topic"], int(r["partition"])): (int(r["positions"]), int(r["end"]))
            for r in rows
        }
        return all(actual.get(key) == (end, end) for key, end in expected.items())

    wait(caught_up, 300)
    for group in ["postgres-handoff-orders", "postgres-handoff-customers"]:
        raw = kafka(
            "/opt/kafka/bin/kafka-consumer-groups.sh",
            "--bootstrap-server",
            "broker1:9092",
            "--describe",
            "--group",
            group,
        )
        (path / (group + ".txt")).write_text(raw)
        rows = [
            line.split()
            for line in raw.splitlines()
            if line.split() and line.split()[0] == group
        ]
        if not rows or any(r[3] != r[4] or r[5] != "0" for r in rows):
            raise RuntimeError("Native consumer offsets did not reach the fence")
    save(
        path / "input-fence.json",
        {
            "positions": [
                {"topic": t, "partition": p, "end": end}
                for (t, p), end in expected.items()
            ]
        },
    )
