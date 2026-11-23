"""DuckDB application SQL over explicit ClickHouse HTTP requests, without scanner."""

import os
import json
import re
import uuid

import recovery_cleanup as cleanup
import duckdb_dependencies as dependencies

import duckdb_clickhouse as legacy
import duckdb_maintenance as maintenance
import recovery_control as control

IMAGE = "iceberg-multi-current-runner-duckdb2-http:dev"
HEADERS = "MAP{'X-ClickHouse-User':'default','X-ClickHouse-Key':'local-reader'}"
COLUMNS = "{'event_id':'BIGINT','customer_id':'INTEGER','amount_cents':'BIGINT','payload_sha256':'VARCHAR'}"
LIMITS = " SETTINGS max_execution_time=30,max_threads=2,max_memory_usage=805306368,max_rows_to_read=30000000,max_bytes_to_read=4294967296,max_result_rows=3000000,max_result_bytes=536870912,result_overflow_mode='throw',timeout_before_checking_execution_speed=0,use_iceberg_metadata_files_cache=0,iceberg_metadata_staleness_ms=0"


def quote(value):
    return "'" + value.replace("'", "''") + "'"


def url(sql):
    return (
        "'http://clickhouse:8123/?wait_end_of_query=1&allow_database_iceberg=1&allow_insert_into_iceberg=1&max_execution_time=30&max_memory_usage=805306368&query=' || url_encode("
        + quote(sql)
        + ")"
    )


def read(sql):
    return (
        "read_json("
        + url(sql + LIMITS + " FORMAT JSONEachRow")
        + ",format='newline_delimited',columns="
        + COLUMNS
        + ",maximum_object_size=65536)"
    )


def connection():
    return [
        "SET home_directory='/home/app'; SET extension_directories=['/opt/duckdb/extensions']; LOAD httpfs; LOAD http_client FROM query_farm;",
        "SET threads=2; SET memory_limit='1GB'; SET temp_directory='/results/spill'; SET max_temp_directory_size='1GB'; SET http_timeout=30; SET http_retries=0;",
        "SET http_client_max_response_size=1048576; SET http_client_max_redirects=0;",
        "CREATE SECRET ch_http (TYPE http,EXTRA_HTTP_HEADERS "
        + HEADERS
        + ",SCOPE 'http://clickhouse:8123/');",
    ]


def read_artifacts(rows):
    if not 1000 <= rows <= 10000000:
        raise ValueError("bounded generated HTTP read fixture required")
    sql = connection()
    generated = f"SELECT toInt64(number) AS event_id,toInt32(number%10000) AS customer_id,toInt64(number%10000+1) AS amount_cents,lower(hex(SHA256(toString(number)))) AS payload_sha256 FROM numbers({rows}) LIMIT {rows}"
    cold = None
    if os.environ.get("HTTP_COLD_CASE"):
        identifier = os.environ["HTTP_COLD_CASE"]
        if str(uuid.UUID(identifier)) != identifier:
            raise ValueError("canonical cold witness case required")
        case = cleanup.regular(
            cleanup.results_root() / "duckdb-connector-concurrent" / identifier,
            cleanup.results_root(),
        )
        config = cleanup.load(case / "concurrent-input.json", case)
        history = cleanup.load(case / "cold-history.json", case)
        expected = config["seed_rows"] + len(history) * 1000
        intervals = sorted((v["first"], v["end"]) for v in history)
        if (
            rows != expected
            or any(v["status"] != "ok" for v in history)
            or intervals
            != [(i, i + 1000) for i in range(config["seed_rows"], expected, 1000)]
        ):
            raise RuntimeError(
                "cold witness does not establish the requested complete input"
            )
        table = config["table"]
        if not re.fullmatch(r"duck_compact_[0-9a-f]{32}", table):
            raise ValueError("owned cold table identifier required")
        current = dependencies.catalog(control.service("storage"), table)
        saved = cleanup.load(case / "cold-catalog-final.json", case)
        if (
            current["metadata"]["table-uuid"] != config["table_uuid"]
            or current["metadata-location"] != saved["metadata-location"]
        ):
            raise RuntimeError("cold source advanced outside its saved witness")
        cold = {"case": identifier, "table": table, "catalog": current, "rows": rows}
        generated = f"SELECT event_id,customer_id,amount_cents,payload_sha256 FROM lake.`lab.{table}` LIMIT {rows + 1}"
    for threads in (1, 2):
        sql += [
            f"SET threads={threads};",
            f"COPY ({maintenance.oracle_query(read(generated), rows, 10000)}) TO '/results/threads-{threads}.json' (FORMAT JSON,ARRAY true);",
        ]
    result = {"run.sql": "\n".join(sql) + "\n", "run.sh": legacy.shell()}
    if cold:
        result["cold-source.json"] = json.dumps(cold, indent=2) + "\n"
    return result


def artifacts(table, rows, hot_only=False):
    if not re.fullmatch(r"duck_compact_[0-9a-f]{32}", table) or rows != 2000000:
        raise ValueError("fresh owned two-million-row HTTP case required")
    sql = connection()

    def execute(statement, name, body=None):
        payload = ",body=>" + body if body else ""
        sql.extend(
            [
                "CREATE TEMP TABLE response AS SELECT http_post("
                + url(statement)
                + ",headers=>"
                + HEADERS
                + payload
                + ") AS value;",
                f"COPY (SELECT value FROM response) TO '/results/{name}-response.json' (FORMAT JSON,ARRAY true);",
                "SELECT CASE WHEN (value->>'status')::INTEGER=200 AND (value->>'body')='' THEN true ELSE error(value::VARCHAR) END FROM response;",
                "DROP TABLE response;",
            ]
        )

    ddl = "(event_id Int64,customer_id Int32,amount_cents Int64,payload_sha256 String) ENGINE=MergeTree ORDER BY (customer_id,event_id)"
    if not hot_only:
        execute(
            "CREATE DATABASE IF NOT EXISTS lake ENGINE=DataLakeCatalog('http://storage:8181','demo','demo') SETTINGS catalog_type='rest',catalog_credential='demo:demo',vended_credentials=false,warehouse='warehouse',storage_endpoint='http://storage:8333/warehouse'",
            "catalog",
        )
    execute(f"CREATE TABLE default.{table} {ddl}", "create")
    for first in range(0, rows, 100000):
        end = first + 100000
        body = f"(SELECT json_object('event_id',list(i),'customer_id',list((i%10000)::INTEGER),'amount_cents',list(i%10000+1),'payload_sha256',list(sha256(CAST(i AS VARCHAR)))) FROM range({first},{end}) t(i))"
        execute(
            f"INSERT INTO default.{table} SETTINGS max_execution_time=30,max_memory_usage=805306368 FORMAT JSONColumns",
            f"insert-{first}",
            body,
        )
        sql.append(
            f"COPY (SELECT {first} AS first_id,{end} AS end_id) TO '/results/ack-{first}.json' (FORMAT JSON,ARRAY true);"
        )
    stages = (
        ("hot", f"default.{table}"),
        ("cold", f"lake.`lab.{table}`"),
        ("restored", f"default.{table}_restored"),
    )
    for stage, source in stages[:1] if hot_only else stages:
        if stage == "cold":
            execute(
                f"INSERT INTO {source} SELECT event_id,customer_id,amount_cents,payload_sha256 FROM default.{table}"
                + LIMITS,
                "publish",
            )
        elif stage == "restored":
            execute(f"CREATE TABLE {source} {ddl}", "create-restored")
            execute(
                f"INSERT INTO {source} SELECT event_id,customer_id,amount_cents,payload_sha256 FROM lake.`lab.{table}`"
                + LIMITS,
                "restore",
            )
        sql.append(
            f"COPY ({maintenance.oracle_query(read(f'SELECT event_id,customer_id,amount_cents,payload_sha256 FROM {source} LIMIT {rows + 1}'), rows, 10000)}) TO '/results/{stage}-rows.json' (FORMAT JSON,ARRAY true);"
        )
        sql.append(legacy.body_gate(f"{stage}-rows", rows))
    sql.append(
        "COPY (SELECT extension_name,extension_version,loaded FROM duckdb_extensions() WHERE loaded ORDER BY extension_name) TO '/results/loaded-extensions.json' (FORMAT JSON,ARRAY true);"
    )
    return {"run.sql": "\n".join(sql) + "\n", "run.sh": legacy.shell()}


setup = legacy.setup
finalize = legacy.finalize


def verify_read(root, rows):
    result = legacy.verify_read(root, rows)
    if (root / "cold-source.json").exists():
        source = cleanup.load(root / "cold-source.json", root)
        current = dependencies.catalog(control.service("storage"), source["table"])
        control.save(root / "cold-catalog-after.json", current)
        if current["metadata-location"] != source["catalog"]["metadata-location"]:
            raise RuntimeError("cold pointer changed during read-only verification")
        result["source"] = (
            "Retained witnessed Iceberg table through ClickHouse HTTP; not generated input"
        )
        result["cold_snapshot_unchanged"] = True
    return result


def verify(root, rows):
    mode = cleanup.load(root / "invocation.json", root)["mode"]
    if mode not in {"connector-http", "connector-http-hot"}:
        raise RuntimeError("unexpected HTTP verification mode")
    loaded = {
        v["extension_name"]: v["extension_version"]
        for v in cleanup.load(root / "loaded-extensions.json", root)
    }
    if (
        loaded.get("http_client") != "9bf6584a9d"
        or loaded.get("httpfs") != "f71cfdb4bf"
        or "clickhouse_scanner" in loaded
    ):
        raise RuntimeError("HTTP extension versions or forbidden scanner differ")
    expected_responses = {"create-response.json"} | {
        f"insert-{first}-response.json" for first in range(0, rows, 100000)
    }
    if mode == "connector-http":
        expected_responses |= {
            "catalog-response.json",
            "publish-response.json",
            "create-restored-response.json",
            "restore-response.json",
        }
    if {p.name for p in root.glob("*-response.json")} != expected_responses:
        raise RuntimeError("HTTP response witness is incomplete")
    for response in root.glob("*-response.json"):
        value = maintenance.single(root, response.name.removesuffix(".json"))["value"]
        if value.get("status") != 200 or value.get("body") != "":
            raise RuntimeError("HTTP mutation not acknowledged cleanly")
    stages = ("hot",) if mode == "connector-http-hot" else ("hot", "cold", "restored")
    for stage in stages:
        maintenance.check_rows(maintenance.single(root, stage + "-rows"), rows)
    for first in range(0, rows, 100000):
        if maintenance.single(root, f"ack-{first}") != {
            "first_id": first,
            "end_id": first + 100000,
        }:
            raise RuntimeError("HTTP acknowledged interval differs")
        with (root / f"ack-{first}.json").open("rb") as witness:
            os.fsync(witness.fileno())
    control.sync_directory(root)
    return {
        "rows_per_stage": rows,
        "stages_verified": list(stages),
        "hot_cold_restored_bodies_correct": len(stages) == 3,
        "application_path": "DuckDB http_client POST and core httpfs JSONEachRow reads; no clickhouse_scanner",
        "production_qualified": False,
    }


def independent_hot(root, table, rows, query):
    sql = f"SELECT count() AS rows,uniqExact(event_id) AS unique_ids,min(event_id) AS first_id,max(event_id) AS last_id,sum(amount_cents) AS amount_total_cents,countIf(event_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL OR payload_sha256 IS NULL OR event_id<0 OR event_id>={rows} OR customer_id!=event_id%10000 OR amount_cents!=event_id%10000+1 OR payload_sha256!=lower(hex(SHA256(toString(event_id))))) AS invalid_bodies FROM default.{table} LIMIT 1 SETTINGS max_execution_time=30,max_memory_usage=805306368,max_rows_to_read=30000000,max_bytes_to_read=4294967296,output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow"
    control.save_text(root / "native-hot.sql", sql)
    raw = control.call(
        [
            "docker",
            "exec",
            query["Id"],
            "clickhouse-client",
            "--password",
            "local-reader",
            "--query",
            sql,
        ],
        timeout=40,
    )
    control.save_text(root / "native-hot.jsonl", raw)
    body = json.loads(raw)
    maintenance.check_rows(body, rows)
    return body
