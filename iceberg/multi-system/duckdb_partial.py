"""Observe remote INSERT effects after a late conversion error; never infer rollback."""

import json
import os
import re

import duckdb_clickhouse as connector
import recovery_control as control


def artifacts(table, rows):
    if (
        not re.fullmatch(r"duck_compact_[0-9a-f]{32}", table)
        or not 1000000 <= rows <= 10000000
    ):
        raise ValueError("fresh bounded partial-insert fixture required")
    sql = connector.connection(rows * 4, 100000)
    sql += [
        f"CALL clickhouse_execute('ch', 'CREATE TABLE default.{table} (event_id Int64, value Int8) ENGINE=MergeTree ORDER BY event_id');",
    ]
    # Intent files are also preserved/fsynced by the host before release. A failed
    # statement has no acknowledged rows, even if native reconciliation finds data.
    for attempt in (1, 2):
        sql += [
            f"COPY (SELECT '{attempt}' AS attempt, current_timestamp AS started_at) TO '/results/attempt-{attempt}-started.json' (FORMAT JSON, ARRAY true);",
            f"INSERT INTO ch.default.{table} SELECT i,CASE WHEN i={rows - 1} THEN 128 ELSE 1 END FROM range({rows}) t(i);",
            f"COPY (SELECT * FROM clickhouse_query('ch','SELECT count() AS rows,uniqExact(event_id) AS unique_ids,countIf(value!=1) AS wrong_values FROM default.{table}')) TO '/results/attempt-{attempt}-effects.json' (FORMAT JSON, ARRAY true);",
        ]
    sql += [
        f"CALL clickhouse_execute('ch', 'CREATE TABLE default.{table}_replay (event_id Int64,value Int8) ENGINE=ReplacingMergeTree ORDER BY event_id');",
        f"CALL clickhouse_execute('ch', 'INSERT INTO default.{table}_replay SELECT * FROM default.{table}');",
    ]
    for attempt in (1, 2):
        sql += [
            "SET enable_profiling='json';",
            f"SET profiling_output='/results/replay-{attempt}-profile.json';",
            f"INSERT INTO ch.default.{table}_replay SELECT i,1 FROM range({rows}) t(i);",
            "PRAGMA disable_profiling;",
            f"COPY (SELECT {attempt} AS attempt,true AS acknowledged) TO '/results/replay-{attempt}-ack.json' (FORMAT JSON,ARRAY true);",
        ]
    sql += [
        f"INSERT INTO ch.default.{table} VALUES ({rows},1);",
        "COPY (SELECT true AS acknowledged) TO '/results/recovery-ack.json' (FORMAT JSON,ARRAY true);",
    ]
    # Continue after the two deliberately invalid statements, but retain the real
    # CLI exit code and every stderr message for host validation.
    shell = (
        connector.shell()
        .replace("-batch -bail -json", "-batch -json")
        .replace('test "$code" -eq 0 || exit 1', 'test "$code" -eq 1 || exit 1')
    )
    return {"run.sql": "\n".join(sql) + "\n", "run.sh": shell}


def check_effects(first, second, final, replay, rows):
    first, second, final = [
        {k: int(v) for k, v in value.items()} for value in (first, second, final)
    ]
    if not (0 < first["rows"] == first["unique_ids"] < rows) or first["wrong_values"]:
        raise RuntimeError("late failure did not establish a valid partial insert")
    if (
        not (
            second["rows"] > first["rows"]
            and 0 < second["unique_ids"] <= rows
            and second["rows"] > second["unique_ids"]
        )
        or second["wrong_values"]
    ):
        raise RuntimeError("blind retry did not establish duplicate effects")
    if final != {
        "rows": second["rows"] + 1,
        "unique_ids": second["unique_ids"] + 1,
        "wrong_values": 0,
        "recovery_markers": 1,
    }:
        raise RuntimeError("native final effects disagree with failed attempts")
    if replay != {"rows": rows, "unique_ids": rows, "wrong_values": 0}:
        raise RuntimeError("corrected key-based replay did not reconcile")


def verify(root, table, rows, query):
    stderr = (root / "native-stderr.log").read_text()
    if (root / "native-exit.txt").read_text().strip() != "1":
        raise RuntimeError("expected native conversion failures did not occur")
    if stderr.count("Conversion Error") != 2 or "out of range" not in stderr:
        raise RuntimeError("unexpected error instead of two late conversion failures")
    if json.loads((root / "recovery-ack.json").read_text()) != [{"acknowledged": True}]:
        raise RuntimeError("connector did not recover for subsequent valid insert")
    sql = f"SELECT count() AS rows,uniqExact(event_id) AS unique_ids,countIf(value!=1 OR event_id<0 OR event_id>{rows}) AS wrong_values,countIf(event_id={rows}) AS recovery_markers FROM default.{table} LIMIT 1 SETTINGS max_execution_time=30,max_rows_to_read={rows * 5},max_bytes_to_read=536870912,max_memory_usage=536870912,timeout_before_checking_execution_speed=0 FORMAT JSONEachRow"
    output = control.call(
        [
            "docker",
            "exec",
            query["Id"],
            "clickhouse-client",
            "--password",
            "local-reader",
            "--query",
            sql,
        ]
    )
    control.save_text(root / "independent-effects.json", output)
    observed = json.loads(output)
    if int(observed["wrong_values"]) or int(observed["recovery_markers"]) != 1:
        raise RuntimeError("invalid persisted values or missing recovery marker")
    first, second = [
        json.loads((root / f"attempt-{n}-effects.json").read_text())[0] for n in (1, 2)
    ]
    for attempt in (1, 2):
        path = root / f"replay-{attempt}-ack.json"
        if json.loads(path.read_text()) != [{"attempt": attempt, "acknowledged": True}]:
            raise RuntimeError("corrected replay did not acknowledge")
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
    replay_sql = f"SELECT count() AS rows,uniqExact(event_id) AS unique_ids,countIf(value!=1 OR event_id<0 OR event_id>={rows}) AS wrong_values FROM default.{table}_replay FINAL LIMIT 1 SETTINGS max_execution_time=30,max_rows_to_read={rows * 5},max_bytes_to_read=536870912,max_memory_usage=536870912,timeout_before_checking_execution_speed=0 FORMAT JSONEachRow"
    replay_output = control.call(
        [
            "docker",
            "exec",
            query["Id"],
            "clickhouse-client",
            "--password",
            "local-reader",
            "--query",
            replay_sql,
        ]
    )
    control.save_text(root / "independent-replay.json", replay_output)
    replay = {key: int(value) for key, value in json.loads(replay_output).items()}
    check_effects(first, second, observed, replay, rows)
    parts_sql = f"SELECT table,sum(rows) AS physical_rows,sum(data_uncompressed_bytes) AS uncompressed_bytes,sum(data_compressed_bytes) AS compressed_bytes,sum(bytes_on_disk) AS allocated_bytes FROM system.parts WHERE active AND database='default' AND table IN ('{table}','{table}_replay') GROUP BY table LIMIT 2 SETTINGS max_execution_time=30,max_rows_to_read=100000,max_bytes_to_read=268435456,timeout_before_checking_execution_speed=0 FORMAT JSONEachRow"
    parts = control.call(
        [
            "docker",
            "exec",
            query["Id"],
            "clickhouse-client",
            "--password",
            "local-reader",
            "--query",
            parts_sql,
        ]
    )
    control.save_text(root / "native-parts.jsonl", parts)
    if len(parts.splitlines()) != 2:
        raise RuntimeError("missing physical storage accounting")
    return {
        "corrected_replay_final": replay,
        "failed_statement_acknowledged_rows": 0,
        "first_failed_statement_effects": first,
        "blind_retry_effects": second,
        "independent_final_effects": observed,
        "production_qualified": False,
        "scope": "Late client conversion failure and blind retry only; corrected immutable-key replay checked with FINAL, not a transactional or exactly-once guarantee; no injected outage.",
    }
