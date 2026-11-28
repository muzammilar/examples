"""Source GTID/input fences, committed output barriers and exact ACK/body oracles."""

import hashlib
import json
import re
import uuid

from lab import ROOT, app, api, fixture, flink, kafka, mysql, save, sql, wait


def allow_writes():
    for table in ["orders", "customers"]:
        mysql(f"GRANT INSERT,UPDATE,DELETE ON orders.{table} TO 'app'@'%'")


def fence_writes():
    for table in ["orders", "customers"]:
        count = int(
            mysql(
                "SELECT COUNT(*) FROM information_schema.table_privileges WHERE "
                "GRANTEE=CONCAT(QUOTE('app'),'@',QUOTE('%')) AND TABLE_SCHEMA='orders' "
                f"AND TABLE_NAME='{table}' AND PRIVILEGE_TYPE IN ('INSERT','UPDATE','DELETE')"
            )
        )
        if count == 3:
            mysql(f"REVOKE INSERT,UPDATE,DELETE ON orders.{table} FROM 'app'@'%'")
        elif count != 0:
            raise RuntimeError(
                "Unexpected partial source write grants; inspect permission drift"
            )


def group_rows(group):
    raw = kafka(
        "/opt/kafka/bin/kafka-consumer-groups.sh",
        "--bootstrap-server",
        "broker1:9092",
        "--describe",
        "--group",
        group,
    )
    return raw, [
        line.split()
        for line in raw.splitlines()
        if line.split() and line.split()[0] == group
    ]


def settle(path):
    # No actor may still be mutating when this cut is taken. Native permissions
    # fence accidental new requests; source errors are never inferred as rollbacks.
    fence_writes()
    gtid = mysql("SELECT @@GLOBAL.gtid_executed")
    if not gtid or not re.fullmatch(r"[0-9a-fA-F:,-]+", gtid):
        raise RuntimeError("Missing or malformed source GTID set")
    # A persisted row coordinate may carry the GTID set preceding its current
    # transaction. A later native log signal makes the entire prior cut observable.
    signal = uuid.uuid4().hex
    mysql(
        f"INSERT INTO debezium_signal VALUES ('{signal}','log',JSON_OBJECT('message','source-fence {signal} at {{}}'))"
    )

    def source_flushed():
        rows = api("/connectors/margins/offsets")["offsets"]
        if len(rows) != 1:
            return False
        observed = rows[0]["offset"].get("gtids", "")
        if not re.fullmatch(r"[0-9a-fA-F:,-]+", observed):
            return False
        return mysql(f"SELECT GTID_SUBSET('{gtid}','{observed}')") == "1"

    wait(source_flushed, 120)
    save(
        path / "source-fence.json",
        {
            "gtid_before_log_signal": gtid,
            "causal_log_signal": signal,
            "offsets": api("/connectors/margins/offsets"),
        },
    )
    for group in ["margins-source-orders", "margins-source-customers"]:

        def caught_up():
            raw, rows = group_rows(group)
            (path / (group + ".txt")).write_text(raw)
            return bool(rows) and all(
                row[3] == row[4] and row[5] == "0" for row in rows
            )

        wait(caught_up, 180)

    job = json.loads((ROOT / "results/job.json").read_text())["id"]
    checkpoint = flink("/jobs/" + job + "/checkpoints")["latest"]["completed"]["id"]
    wait(
        lambda: flink("/jobs/" + job + "/checkpoints")["latest"]["completed"]["id"]
        > checkpoint,
        120,
    )
    run = uuid.uuid4().hex
    records = [r for r in app("fence", run, path) if r.get("event") == "fence"]
    if len(records) != 4 or {r["partition"] for r in records} != set(range(4)):
        raise RuntimeError("Incomplete committed output fence")
    ends = {int(r["partition"]): int(r["offset"]) for r in records}

    def output_committed():
        raw, rows = group_rows("mysql-margins-capture")
        (path / "output-group.txt").write_text(raw)
        observed = {
            int(r[2]): int(r[3])
            for r in rows
            if r[1] == "margins.enriched" and r[3].isdigit()
        }
        return all(
            observed.get(partition, -1) > offset for partition, offset in ends.items()
        )

    wait(output_committed, 180)
    for host in ["ch1", "ch2", "ch3"]:
        command = "SYSTEM SYNC REPLICA pipeline.margins_state"
        sql(command, host)
        rows = sql(
            f"SELECT partition,max(offset) AS last_offset,uniqExact(offset) AS copies FROM pipeline.history WHERE topic='margins.enriched' AND JSONExtractString(payload,'op')='barrier' AND JSONExtractString(payload,'id')='{run}' GROUP BY partition",
            host,
        )
        if {int(r["partition"]): int(r["last_offset"]) for r in rows} != ends or any(
            int(r["copies"]) != 1 for r in rows
        ):
            raise RuntimeError("Output barriers missing on " + host)
    partition_changes = sql(
        "SELECT count() AS n FROM (SELECT JSONExtractUInt(if(JSONExtractString(payload,'op')='d',JSONExtractRaw(payload,'before'),JSONExtractRaw(payload,'after')),'order_id') AS order_id,uniqExact(partition) AS n FROM pipeline.history WHERE topic='margins.enriched' AND JSONExtractString(payload,'op') IN ('c','u','d') GROUP BY order_id HAVING n!=1)"
    )[0]["n"]
    if int(partition_changes):
        raise RuntimeError(
            "An order changed Kafka partitions; offset versioning is invalid"
        )
    save(
        path / "output-fence.json",
        {
            "id": run,
            "records": records,
            "checkpoint_before_fence": checkpoint,
            "partition_changes": 0,
        },
    )


def reconcile(path):
    bodies = [
        r for r in app("verify", uuid.uuid4().hex, path) if r.get("event") == "verified"
    ]
    if len(bodies) != 3 or len({r["body_sha256"] for r in bodies}) != 1:
        raise RuntimeError("Incomplete fixed-replica verification")
    receipts = json.loads(
        mysql(
            "SELECT COALESCE(JSON_ARRAYAGG(JSON_ARRAY(id,operation,first_id,last_id,affected)),JSON_ARRAY()) FROM receipts"
        )
    )
    receipt_map = {r[0]: r[1:] for r in receipts}
    inserted = json.loads(
        mysql(
            "SELECT COALESCE(JSON_ARRAYAGG(JSON_ARRAY(r.id,o.id,o.customer_id,o.amount_cents,o.cost_cents,o.event_second)),JSON_ARRAY()) FROM receipts r JOIN orders o ON r.first_id=o.id WHERE r.operation='live'"
        )
    )
    inserted_map = {r[0]: r[1:] for r in inserted}
    histories, seen = [], set()
    for case in sorted((ROOT / "results/cases").glob("*/case.json")):
        metadata = json.loads(case.read_text())
        if metadata["fixture"] != fixture()["id"]:
            continue
        for name in ["seed.jsonl", "witness.jsonl", "correction.jsonl"]:
            journal = case.parent / name
            if not journal.exists():
                continue
            intents, acks = {}, []
            for row in map(json.loads, journal.read_text().splitlines()):
                key = row.get("sequence", 1)
                if row["event"] == "intent":
                    if key in intents:
                        raise RuntimeError("Duplicate intent")
                    intents[key] = row
                elif row["event"] == "ack":
                    if key not in intents or row["receipt"] in seen:
                        raise RuntimeError("Duplicate or unpaired acknowledgement")
                    if any(
                        row.get(k) != v for k, v in intents[key].items() if k != "event"
                    ):
                        raise RuntimeError("ACK differs from its durable intent")
                    seen.add(row["receipt"])
                    if name == "witness.jsonl":
                        body = [
                            row[k]
                            for k in [
                                "id",
                                "customer_id",
                                "amount_cents",
                                "cost_cents",
                                "event_second",
                            ]
                        ]
                        digest = hashlib.sha256(
                            ":".join(map(str, body)).encode()
                        ).hexdigest()
                        if (
                            digest != row["sha256"]
                            or inserted_map.get(row["receipt"]) != body
                        ):
                            raise RuntimeError(
                                "Acknowledged immutable insert body missing or wrong"
                            )
                        expected = ["live", row["id"], row["id"], 1]
                    elif name == "seed.jsonl":
                        expected = ["seed", 1, row["orders"], row["orders"]]
                    else:
                        expected = ["shipping", 10001, 11000, 1000]
                    if receipt_map.get(row["receipt"]) != expected:
                        raise RuntimeError(
                            "Acknowledged atomic source receipt missing or wrong"
                        )
                    acks.append(row)
                else:
                    raise RuntimeError(
                        "Ambiguous mutation requires retained-state diagnosis"
                    )
            if not intents or len(intents) != len(acks):
                raise RuntimeError("Incomplete durable witness")
            histories.append(
                {"path": str(journal.relative_to(ROOT)), "acks": len(acks)}
            )
    if not histories:
        raise RuntimeError("No source witness")
    save(
        path / "reconciled.json",
        {
            "replicas": bodies,
            "histories": histories,
            "total_acknowledged_transactions": sum(r["acks"] for r in histories),
            "wrong_or_missing": 0,
        },
    )
    return bodies
