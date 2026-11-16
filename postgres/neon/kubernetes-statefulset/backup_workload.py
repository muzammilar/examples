"""Consistent pg_dump snapshot and independent logical-restore oracle.

Executed in the example-owned workload pod through stdin; native pg_dump/restore
and psycopg come from its Docker image. Backup files live on the external witness.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psycopg

import workload
from recovery import digest

root = Path("/results")
action, case = sys.argv[1:]
if Path(case).name != case:
    raise ValueError("expected simple case name")
archive = root / (case + ".dump")
manifest = root / (case + "-snapshot.json")


def save(path, value):
    with path.open("x") as output:
        json.dump(value, output)
        output.flush()
        os.fsync(output.fileno())


if action == "backup":
    if archive.exists() or manifest.exists():
        raise RuntimeError("backup exists; preserve it instead of overwriting")
    with psycopg.connect(os.environ["NEON_DSN"], autocommit=True) as db:
        db.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        snapshot_started = time.time()
        snapshot = db.execute("SELECT pg_export_snapshot()").fetchone()[0]
        expected = [
            row[0]
            for row in db.execute(
                "SELECT event_id FROM recovery_events ORDER BY event_id"
            )
        ]
        attempted, acked_before_snapshot = set(), set()
        for path in root.glob("fault-*.jsonl"):
            for line in path.read_text().split("\n")[:-1]:
                row = json.loads(line)
                keys = range(row["first"], row["first"] + row["count"])
                if row["stage"] == "attempt":
                    attempted.update(keys)
                elif row["stage"] == "ack" and row["at"] <= snapshot_started:
                    acked_before_snapshot.update(keys)
        if not set(expected).issubset(attempted) or not acked_before_snapshot.issubset(
            expected
        ):
            raise RuntimeError(
                "source snapshot contradicts the external attempt/ACK witness"
            )
        subprocess.run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--table=public.orders",
                "--table=public.recovery_events",
                "--strict-names",
                "--snapshot=" + snapshot,
                "--dbname=" + os.environ["NEON_DSN"],
                "--file=" + str(archive),
            ],
            check=True,
        )
        with archive.open("rb") as data:
            os.fsync(data.fileno())
            checksum = hashlib.file_digest(data, "sha256").hexdigest()
        save(
            manifest,
            {
                "snapshot": snapshot,
                "snapshot_started": snapshot_started,
                "acked_before_snapshot": len(acked_before_snapshot),
                "recovery_ids": expected,
                "dump_bytes": archive.stat().st_size,
                "sha256": checksum,
            },
        )
    print("Durable synchronized snapshot:", len(expected), "recovery rows")
elif action == "restore":
    state = json.loads(manifest.read_text())
    with archive.open("rb") as data:
        if hashlib.file_digest(data, "sha256").hexdigest() != state["sha256"]:
            raise RuntimeError("backup checksum mismatch")
    dsn = "postgresql://restore@restore:5432/postgres"
    subprocess.run(
        [
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            "--dbname=" + dsn,
            str(archive),
        ],
        check=True,
    )
    baseline = workload.verify_dsn(dsn)
    seen = set()
    expected = set(state["recovery_ids"])
    with psycopg.connect(dsn) as db:
        with db.cursor(name="restored_recovery_rows") as cursor:
            cursor.itersize = 10000
            cursor.execute(
                "SELECT event_id,customer_id,amount,payload FROM recovery_events"
            )
            for key, customer, amount, payload in cursor:
                if (
                    key not in expected
                    or key in seen
                    or customer != key % 10000
                    or amount != key * 3
                    or bytes(payload) != digest(key)
                ):
                    raise RuntimeError(
                        "corrupt, duplicate or unexpected restored row: " + str(key)
                    )
                seen.add(key)

    with psycopg.connect(dsn, autocommit=True) as db:
        db.execute("ALTER SYSTEM SET default_transaction_read_only = on")
        db.execute("SELECT pg_reload_conf()")
    if seen != expected:
        raise RuntimeError("restored snapshot is incomplete")
    deadline = time.monotonic() + 5
    while True:
        with psycopg.connect(dsn, autocommit=True) as db:
            read_only = db.execute("SHOW default_transaction_read_only").fetchone()[0]
        if read_only == "on":
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("restored instance did not become read-only")
        time.sleep(0.1)
    with psycopg.connect(dsn) as db:
        try:
            db.execute("UPDATE orders SET amount=amount WHERE false")
        except psycopg.Error as error:
            if error.sqlstate != "25006":
                raise
        else:
            raise RuntimeError("restored instance accepted a write command")
    acknowledged = set()
    for path in root.glob("fault-*.jsonl"):
        for line in path.read_text().split("\n")[:-1]:
            record = json.loads(line)
            if record["stage"] == "ack":
                acknowledged.update(
                    range(record["first"], record["first"] + record["count"])
                )
    excluded_tail = acknowledged - expected
    save(
        root / (case + "-restore.json"),
        {
            "baseline": baseline,
            "snapshot_recovery_rows": len(seen),
            "acknowledged_rows_in_snapshot": len(acknowledged & expected),
            "acknowledged_rows_after_snapshot_not_restored": len(excluded_tail),
            "missing_snapshot_rows": 0,
            "wrong_or_duplicate": 0,
            "effective_default_read_only": True,
            "rejected_write_sqlstate": "25006",
            "dump_bytes": state["dump_bytes"],
            "sha256": state["sha256"],
            "scope": "logical Neon SQL backup restored into an independent read-only PostgreSQL instance; not physical Neon control-plane recovery",
        },
    )
    print(
        "Complete logical restore readback passed; post-snapshot ACK tail:",
        len(excluded_tail),
    )
else:
    raise ValueError(action)
