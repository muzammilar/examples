"""Continuous quorum inserts with an external attempt/ACK journal, no replay."""

from datetime import datetime, timezone
import json
import os
import sys
import time
import workload as w

BATCH = 10000


def records():
    history = w.ROOT / "fault-history.json"
    result = json.loads(history.read_text()) if history.exists() else []
    journal = w.ROOT / "churn.jsonl"
    if journal.exists():
        result.extend(json.loads(line) for line in journal.read_text().splitlines())
    return result


def churn(phase):
    if phase not in [0, 1, 2]:
        raise ValueError("unknown fault phase")
    start = w.ROWS + phase * 10000000
    end = time.monotonic() + 600
    with (w.ROOT / "churn.jsonl").open("x") as f:

        def append(value):
            value["time"] = time.monotonic()
            f.write(json.dumps(value) + "\n")
            f.flush()
            os.fsync(f.fileno())

        for index in range(600):
            if time.monotonic() > end or (w.ROOT / "stop-churn").exists():
                break
            first = start + index * BATCH
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            append(
                {"stage": "attempt", "first": first, "count": BATCH, "timestamp": stamp}
            )
            try:
                w.insert(first, BATCH, stamp, node=1)
                append({"stage": "ack", "first": first})
            except Exception as error:
                append(
                    {"stage": "ambiguous", "first": first, "error": str(error)[:500]}
                )
            try:
                old = json.loads((w.ROOT / "seed.json").read_text())["timestamps"][
                    "old"
                ]
                rows = w.query(
                    f"SELECT count() AS n FROM lab.events WHERE tenant_id=42 AND event_time=toDateTime('{old}','UTC') AND event_id<{w.ROWS} LIMIT 1 FORMAT JSON",
                    2,
                    {"enable_filesystem_cache": 0, "use_query_cache": 0},
                )
                if int(rows[0]["n"]) != w.ROWS // 20000:
                    raise RuntimeError("wrong cold-query count")
                append({"stage": "get"})
            except Exception as error:
                append({"stage": "get_error", "error": str(error)[:500]})
            time.sleep(1)


def verify():
    w.sync()
    expected = json.loads((w.ROOT / "expected.json").read_text())
    entries = records()
    attempts = {r["first"]: r for r in entries if r["stage"] == "attempt"}
    acks = {r["first"] for r in entries if r["stage"] == "ack"}
    observed = []
    for node in range(3):
        w.checks(node, expected)
        rows = w.query(
            f"""SELECT intDiv(event_id-{w.ROWS},{BATCH}) AS batch,
            count() AS n, uniqExact(event_id) AS unique_ids, min(event_id) AS first,
            max(event_id) AS last, sum(event_id) AS id_sum, sum(amount) AS amount_sum,
            min(event_time) AS first_time, max(event_time) AS last_time,
            countIf(tenant_id!=event_id%10000 OR amount!=(event_id*17+intDiv(event_id,10000)*31)%10000
                OR event_type!=['view','click','purchase','logout'][1+intDiv(event_id,10000)%4]
                OR payload!=SHA256(toString(event_id))) AS invalid
            FROM lab.events WHERE event_id>={w.ROWS} AND event_time>=toDateTime('2020-01-01')
            GROUP BY batch LIMIT 5000 FORMAT JSON""",
            node,
        )
        seen = set()
        for r in rows:
            first = int(r["first"])
            entry = attempts.get(first)
            if entry is None:
                raise RuntimeError("unexpected event batch")
            count = entry["count"]
            checks = {
                "n": count,
                "unique_ids": count,
                "last": first + count - 1,
                "id_sum": count * first + count * (count - 1) // 2,
                "amount_sum": count // 10000 * 49995000,
                "invalid": 0,
            }
            if (
                any(int(r[k]) != v for k, v in checks.items())
                or r["first_time"] != entry["timestamp"]
                or r["last_time"] != entry["timestamp"]
            ):
                raise RuntimeError(
                    f"corrupt/partial event batch on replica {node}: {r}"
                )
            seen.add(first)
        if acks - seen:
            raise RuntimeError(
                f"missing acknowledged batches on replica {node}: {acks - seen}"
            )
        observed.append(seen)
    if any(seen != observed[0] for seen in observed[1:]):
        raise RuntimeError("replicas disagree on ambiguous batches")
    w.check_parts()
    result = {
        "replicas": 3,
        "baseline_rows_per_replica": w.ROWS,
        "fault_rows_per_replica": sum(attempts[k]["count"] for k in observed[0]),
        "acknowledged_batches": len(acks),
        "acknowledged_rows": sum(attempts[k]["count"] for k in acks),
        "missing_acknowledged_batches": 0,
        "invalid_rows": 0,
        "duplicate_ids": 0,
        "replica_disagreements": 0,
    }
    w.save("reconciliation", result)
    print(json.dumps(result, indent=2), flush=True)


def rejoin():
    w.sync()
    end = time.monotonic() + 600
    while time.monotonic() < end:
        cold = [
            int(
                w.query(
                    "SELECT sum(rows) AS n FROM system.parts WHERE database='lab' AND table='events' AND active AND disk_name='remote_cache' LIMIT 1 FORMAT JSON",
                    node,
                )[0]["n"]
            )
            for node in range(3)
        ]
        if all(n >= w.ROWS // 2 for n in cold):
            w.save("cold-rejoin", {"cold_rows_per_replica": cold})
            return
        time.sleep(2)
    raise RuntimeError("cold S3 parts did not rejoin on every replica")


def drop_replica():
    end = time.monotonic() + 180
    while time.monotonic() < end:
        active = int(
            w.query(
                "SELECT count() AS n FROM system.zookeeper WHERE path='/clickhouse/tables/01/events/replicas/replica0' AND name='is_active' LIMIT 1 FORMAT JSON",
                1,
            )[0]["n"]
        )
        if not active:
            w.query("SYSTEM DROP REPLICA 'replica0' FROM TABLE lab.events", 1)
            return
        time.sleep(2)
    raise RuntimeError("old replica session is still active")


def restore():
    w.query("CREATE DATABASE IF NOT EXISTS lab", 0)
    statement = w.query("SHOW CREATE TABLE lab.events FORMAT JSON", 1)[0]["statement"]
    if "'replica1'" in statement:
        statement = statement.replace("'replica1'", "'{replica}'")
    if "'{replica}'" not in statement:
        raise RuntimeError("unexpected native replica DDL")
    w.query(statement, 0)
    rejoin()


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "churn":
        churn(int(sys.argv[2]))
    elif action == "verify":
        verify()
    elif action == "rejoin":
        rejoin()
    elif action == "drop-replica":
        drop_replica()
    elif action == "restore":
        restore()
    elif action == "clock":
        print(time.monotonic(), flush=True)
    else:
        raise ValueError(action)
