"""Independent native DuckDB SQL writers/readers around one owned ClickHouse crash."""

import hashlib
import json
import re
import subprocess
import threading
import time

import duckdb_clickhouse as connector
import recovery_control as control

BATCH = 10000


def artifacts(table, rows):
    if not re.fullmatch(r"duck_compact_[0-9a-f]{32}", table) or not (
        1000000 <= rows <= 10000000 and rows % BATCH == 0
    ):
        raise ValueError("fresh table and whole bounded witness batches required")
    return {
        "run.sh": "#!/bin/sh\nwhile ! test -f /results/native-finish; do sleep .1; done\n"
    }


def expected(first, count):
    return [
        {
            "event_id": i,
            "customer_id": i % 10000,
            "amount_cents": i % 10000 + 1,
            "payload_sha256": hashlib.sha256(str(i).encode()).hexdigest(),
        }
        for i in range(first, first + count)
    ]


def reconcile(groups, intents):
    observed = {
        int(row["batch"]): {k: int(v) for k, v in row.items()} for row in groups
    }
    if len(observed) != len(groups):
        raise RuntimeError("duplicate native batch summaries")
    allowed, acknowledged, ambiguous_applied = set(), 0, 0
    for intent in intents:
        for first in range(intent["first"], intent["end"], BATCH):
            group = observed.get(first // BATCH)
            allowed.add(first // BATCH)
            if group is not None and (
                group["invalid_bodies"]
                or group["rows"] != group["unique_ids"]
                or not first <= group["first_id"] <= group["last_id"] < first + BATCH
            ):
                raise RuntimeError("corrupt, duplicate or misplaced native bodies")
            if intent["status"] == "ok":
                if group is None or group["rows"] != BATCH:
                    raise RuntimeError("acknowledged batch missing after restart")
                acknowledged += BATCH
            elif group is not None:
                ambiguous_applied += group["rows"]
    if set(observed) - allowed:
        raise RuntimeError("rows outside the external intent witness")
    return {
        "acknowledged_rows_verified": acknowledged,
        "acknowledged_missing_rows": 0,
        "wrong_or_duplicate_rows": 0,
        "unacknowledged_applied_rows": ambiguous_applied,
    }


def drive(root, table, rows, identifier, query, sampler):
    stop, lock = threading.Event(), threading.Lock()
    phase, records, errors = ["baseline"], [], []
    prefix = connector.connection(max(rows * 2, 10000000), 100000)
    prefix.append("SET ch_connect_timeout_ms=1000; SET ch_receive_timeout_ms=3000;")

    def request(role, sequence, statement, first=0, end=0):
        with lock:
            label = phase[0]
        stem = f"{role}-{sequence:06d}"
        intent = {
            "role": role,
            "sequence": sequence,
            "phase": label,
            "first": first,
            "end": end,
            "started_ns": time.time_ns(),
            "sql_sha256": hashlib.sha256(statement.encode()).hexdigest(),
        }
        control.save(root / (stem + "-intent.json"), intent)
        sql = (
            ".output /dev/null\n"
            + "\n".join(prefix)
            + "\n.output stdout\n"
            + statement
            + "\n"
        )
        control.save_text(root / (stem + ".sql"), sql)
        start = time.monotonic_ns()
        try:
            value = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    identifier,
                    "duckdb",
                    "-batch",
                    "-bail",
                    "-json",
                    ":memory:",
                ],
                input=sql,
                env=control.DOCKER_ENV,
                text=True,
                capture_output=True,
                timeout=12,
            )
            output, stderr, code = value.stdout, value.stderr, value.returncode
            status = "ok" if code == 0 else "ambiguous" if role != "reader" else "fail"
        except subprocess.TimeoutExpired as error:
            output, stderr, code, status = (
                "",
                str(error),
                None,
                "ambiguous" if role != "reader" else "fail",
            )
        control.save_text(root / (stem + "-stdout.json"), output)
        control.save_text(root / (stem + "-stderr.log"), stderr)
        result = intent | {
            "ended_ns": time.time_ns(),
            "duration_ns": time.monotonic_ns() - start,
            "exit_code": code,
            "status": status,
        }
        if role == "reader" and status == "ok":
            if json.loads(output) != expected(first, end - first):
                result["status"] = "mismatch"
        control.save(root / (stem + "-result.json"), result)
        with lock:
            records.append(result)
        return result

    def insert(first, end):
        return f"INSERT INTO ch.default.{table} SELECT i,CAST(i%10000 AS INTEGER),i%10000+1,sha256(CAST(i AS VARCHAR)) FROM range({first},{end}) t(i);"

    def writer():
        try:
            sequence = 0
            while not stop.is_set():
                first = rows + sequence * BATCH
                if first >= rows + 5000000:
                    raise RuntimeError(
                        "writer exhausted its bounded input before recovery"
                    )
                request(
                    "writer",
                    sequence,
                    insert(first, first + BATCH),
                    first,
                    first + BATCH,
                )
                sequence += 1
                stop.wait(0.2)
        except BaseException as error:
            with lock:
                errors.append(repr(error))
            stop.set()

    def reader():
        try:
            sequence = 0
            while not stop.is_set():
                with lock:
                    successful = [
                        r
                        for r in records
                        if r["role"] == "writer" and r["status"] == "ok"
                    ]
                first = successful[-1]["first"] if sequence % 2 and successful else 0
                statement = f"SELECT event_id,customer_id,amount_cents,payload_sha256 FROM ch.default.{table} WHERE event_id>={first} AND event_id<{first + 1000} ORDER BY event_id LIMIT 1000;"
                request("reader", sequence, statement, first, first + 1000)
                sequence += 1
                stop.wait(0.2)
        except BaseException as error:
            with lock:
                errors.append(repr(error))
            stop.set()

    def progress(label, minimum=2, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with lock:
                current = list(records)
                failures = list(errors)
            if failures or any(r["status"] == "mismatch" for r in current):
                raise RuntimeError("live actor failure/mismatch: " + repr(failures))
            if all(
                sum(
                    r["phase"] == label and r["role"] == role and r["status"] == "ok"
                    for r in current
                )
                >= minimum
                for role in ("writer", "reader")
            ):
                return
            time.sleep(0.1)
        raise TimeoutError("no independent read/write progress in " + label)

    control.save(root / "target-before.json", control.state(query))
    storage = control.service("storage")
    control.save(root / "storage-before.json", control.state(storage))
    seed = request(
        "seed",
        0,
        f"CALL clickhouse_execute('ch','CREATE TABLE default.{table} (event_id Int64,customer_id Int32,amount_cents Int64,payload_sha256 String) ENGINE=MergeTree ORDER BY (customer_id,event_id)'); "
        + insert(0, rows),
        0,
        rows,
    )
    if seed["status"] != "ok":
        raise RuntimeError("seed not acknowledged; preserve ambiguous native effects")
    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    failure, killed = None, False
    try:
        for thread in threads:
            thread.start()
        progress("baseline")
        control.wait_coverage(
            sampler, ["storage", "clickhouse", "duckdb-cli"], time.time_ns()
        )
        with lock:
            phase[0] = "outage"
        control.save(
            root / "fault-intent.json",
            {
                "signal": "SIGKILL",
                "container": query["Id"],
                "started_ns": time.time_ns(),
            },
        )
        control.call(
            [
                "docker",
                "kill",
                "--signal",
                "KILL",
                control.owned(query["Id"], "clickhouse")["Id"],
            ]
        )
        killed = True
        fenced = control.owned(query["Id"], "clickhouse")
        control.save(root / "target-fenced.json", control.state(fenced))
        control.require_fenced(fenced)
        # The native process stays down while both independent actors continue.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if stop.wait(0.1):
                raise RuntimeError("live actor stopped during outage")
        control.save(root / "restart-intent.json", {"started_ns": time.time_ns()})
        control.call(["docker", "start", query["Id"]])
        with lock:
            phase[0] = "recovery"
        progress("recovery", minimum=4)
        with lock:
            phase[0] = "after"
        progress("after", minimum=4)
    except BaseException as error:
        failure = error
    finally:
        stop.set()
        for thread in threads:
            if thread.ident:
                thread.join(timeout=15)
        actors_alive = any(thread.is_alive() for thread in threads)
        current = control.owned(query["Id"], "clickhouse")
        if killed and not current["State"]["Running"]:
            control.call(["docker", "start", query["Id"]])
        control.save(
            root / "target-after.json",
            control.state(control.owned(query["Id"], "clickhouse")),
        )
        with lock:
            history = list(records)
        control.save(root / "history.json", history)
        if actors_alive:
            raise RuntimeError(
                "live actor did not stop; native runner requires fencing"
            )
    if failure:
        raise failure
    if (
        errors
        or any(r["status"] == "mismatch" for r in records)
        or not all(
            any(
                r["phase"] == "outage" and r["role"] == role and r["status"] != "ok"
                for r in records
            )
            for role in ("writer", "reader")
        )
    ):
        raise RuntimeError("outage not observed by both live actors")
    if control.live_identity(control.service("storage")) != control.live_identity(
        storage
    ):
        raise RuntimeError("unrelated storage process restarted")
    after = control.owned(query["Id"], "clickhouse")
    if (
        not after["State"]["Running"]
        or after["State"]["StartedAt"] == query["State"]["StartedAt"]
    ):
        raise RuntimeError("new ClickHouse process not established")
    return after


def verify(root, table, rows, query):
    history = json.loads((root / "history.json").read_text())
    # All client processes have returned. Require no UUID-owned insert remains
    # active before interpreting absent data from failed/ambiguous requests.
    active_sql = f"SELECT count() FROM system.processes WHERE startsWith(query,'INSERT INTO') AND position(query,'{table}')>0 LIMIT 1 SETTINGS max_execution_time=10 FORMAT TSV"
    client = [
        "docker",
        "exec",
        query["Id"],
        "clickhouse-client",
        "--password",
        "local-reader",
        "--query",
    ]
    if control.call(client + [active_sql]).strip() != "0":
        raise RuntimeError(
            "owned insert still active; reconciliation would be premature"
        )
    sql = f"SELECT intDiv(event_id,{BATCH}) AS batch,count() AS rows,uniqExact(event_id) AS unique_ids,min(event_id) AS first_id,max(event_id) AS last_id,countIf(event_id<0 OR customer_id!=event_id%10000 OR amount_cents!=event_id%10000+1 OR payload_sha256!=lower(hex(SHA256(toString(event_id))))) AS invalid_bodies FROM default.{table} GROUP BY batch ORDER BY batch LIMIT 2000 SETTINGS max_execution_time=120,max_memory_usage=805306368,max_threads=2,max_rows_to_read=30000000,max_bytes_to_read=4294967296,timeout_before_checking_execution_speed=0 FORMAT JSONEachRow"
    control.save_text(root / "native-reconcile.sql", sql)
    output = control.call(client + [sql], timeout=130)
    control.save_text(root / "native-reconcile.jsonl", output)
    checks = reconcile(
        [json.loads(line) for line in output.splitlines()],
        [r for r in history if r["role"] != "reader"],
    )
    parts = f"SELECT sum(rows) AS rows,sum(data_uncompressed_bytes) AS uncompressed_bytes,sum(data_compressed_bytes) AS compressed_bytes,sum(bytes_on_disk) AS allocated_bytes FROM system.parts WHERE active AND database='default' AND table='{table}' LIMIT 1 SETTINGS max_execution_time=30,max_rows_to_read=100000,timeout_before_checking_execution_speed=0 FORMAT JSONEachRow"
    control.save_text(root / "native-parts.json", control.call(client + [parts]))
    return checks | {
        "production_qualified": False,
        "scope": "Single-node retained-volume SIGKILL with fresh native DuckDB process per request; no persistent-session reconnect, physical power loss, S3 outage or multi-replica availability claim.",
    }
