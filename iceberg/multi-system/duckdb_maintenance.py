"""Qualify DuckDB-native v2 rewrites before live-fault experiments."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid

import cold_readback
import cold_storage
import recovery_cleanup as cleanup
import recovery_control as control
from recovery_resources import ResourceSampler


def oracle_query(source, rows, customers):
    return f"""SELECT count(*) AS rows, count(DISTINCT event_id) AS unique_ids,
min(event_id) AS first_id, max(event_id) AS last_id,
sum(amount_cents) AS amount_total_cents,
count(*) FILTER (WHERE event_id IS NULL OR customer_id IS NULL
 OR amount_cents IS NULL OR payload_sha256 IS NULL
 OR event_id < 0 OR event_id >= {rows} OR customer_id != event_id % {customers}
 OR amount_cents != event_id % 10000 + 1
 OR payload_sha256 != sha256(CAST(event_id AS VARCHAR))) AS invalid_bodies
FROM {source}"""


def program(table, rows, customers, batch):
    if not re.fullmatch(r"duck_compact_[0-9a-f]{32}", table):
        raise ValueError("fresh owned table identifier required")
    if not (1 <= customers <= rows <= 10000000 and 1 <= batch <= rows // 5):
        raise ValueError("require positive bounded counts and at least five batches")
    qualified = "lake.lab." + table
    sql = [
        "SET threads=2; SET memory_limit='1GB';",
        "SET temp_directory='/results/spill'; SET max_temp_directory_size='1GB';",
        "SET extension_directories=['/opt/duckdb/extensions']; LOAD httpfs; LOAD avro; LOAD iceberg;",
        "SELECT CASE WHEN version()='v2.0.1' THEN true ELSE error('require native v2.0.1') END;",
        "COPY (SELECT version() AS engine, platform FROM pragma_platform()) TO '/results/native.json' (FORMAT JSON, ARRAY true);",
        "COPY (SELECT extension_name,extension_version,install_path,loaded FROM duckdb_extensions() WHERE extension_name IN ('avro','httpfs','iceberg') ORDER BY extension_name) TO '/results/extensions.json' (FORMAT JSON, ARRAY true);",
        "CREATE SECRET local_s3 (TYPE S3, KEY_ID 'demo', SECRET 'demo', ENDPOINT 'storage:8333', URL_STYLE 'path', USE_SSL false);",
        "CREATE SECRET local_catalog (TYPE ICEBERG, URI 'http://storage:8181', CLIENT_ID 'demo', CLIENT_SECRET 'demo');",
        "ATTACH 's3://warehouse' AS lake (TYPE iceberg, SECRET local_catalog, URI 'http://storage:8181', READ_ONLY false);",
        "CREATE SCHEMA IF NOT EXISTS lake.lab;",
        f"CREATE TABLE {qualified} (event_id BIGINT,customer_id INTEGER,amount_cents BIGINT,payload_sha256 VARCHAR) WITH ('format-version'='2');",
    ]

    def copy(query, name):
        sql.append(
            f"COPY ({query}) TO '/results/{name}.json' (FORMAT JSON, ARRAY true);"
        )

    for lo in range(0, rows, batch):
        hi = min(lo + batch, rows)
        sql.append(
            f"INSERT INTO {qualified} SELECT i,CAST(i%{customers} AS INTEGER),i%10000+1,sha256(CAST(i AS VARCHAR)) FROM range({lo},{hi}) t(i);"
        )
        copy(f"SELECT {lo} AS first_id,{hi} AS end_id", f"ack-{lo}")

    def catalog(name):
        copy(
            f"SELECT metadata_location,metadata::JSON AS metadata FROM iceberg_load_table_response('{qualified}')",
            name + "-catalog",
        )

    def state(name, source=qualified):
        copy(oracle_query(source, rows, customers), name + "-rows")
        catalog(name)

    def files(name):
        copy(f"SELECT * FROM iceberg_metadata('{qualified}')", name + "-files")
        sql.append(
            f"SET VARIABLE live_files=(SELECT list(DISTINCT file_path ORDER BY file_path) FROM iceberg_metadata('{qualified}') WHERE status <> 'DELETED' AND content='DATA');"
        )
        copy(
            "SELECT count(*) AS column_chunks,sum(total_compressed_size) AS compressed_bytes,sum(total_uncompressed_size) AS uncompressed_bytes FROM parquet_metadata(getvariable('live_files'))",
            name + "-compression",
        )
        copy(
            "SELECT sum(num_rows) AS rows,sum(file_size_bytes) AS file_bytes,sum(footer_size) AS footer_bytes FROM parquet_file_metadata(getvariable('live_files'))",
            name + "-parquet",
        )

    def rewrite(name):
        sql.extend(
            [
                "SET enable_profiling='json';",
                f"SET profiling_output='/results/{name}-profile.json';",
                f"CALL iceberg_rewrite_data_files('{qualified}',target_file_size_bytes=>134217728,min_input_files=>2);",
                "PRAGMA disable_profiling;",
            ]
        )

    state("before")
    files("before")
    sql.append(
        f"SET VARIABLE old_metadata=(SELECT metadata_location FROM iceberg_load_table_response('{qualified}'));"
    )
    sql.append(
        f"SET VARIABLE old_snapshot=(SELECT snapshot_id FROM iceberg_snapshots('{qualified}') ORDER BY sequence_number DESC LIMIT 1);"
    )
    sql.append("BEGIN;")
    rewrite("rollback")
    copy(oracle_query(qualified, rows, customers), "inside-transaction-rows")
    sql.append("ROLLBACK;")
    state("rolled-back")
    rewrite("committed")
    state("committed")
    files("committed")
    copy(
        oracle_query(
            "iceberg_scan(getvariable('old_metadata'),snapshot_from_id=CAST(getvariable('old_snapshot') AS UBIGINT))",
            rows,
            customers,
        ),
        "retained-snapshot-rows",
    )
    rewrite("noop")
    state("noop")
    sql.extend(
        [
            "BEGIN;",
            f"UPDATE {qualified} SET payload_sha256='wrong-body-control' WHERE event_id=0;",
        ]
    )
    copy(oracle_query(qualified, rows, customers), "negative-body-rows")
    sql.append("ROLLBACK;")
    state("negative-rolled-back")
    return "\n".join(sql) + "\n"


def single(root, name):
    (value,) = cleanup.load(root / (name + ".json"), root)
    return value


def check_rows(value, rows):
    expected_sum = rows // 10000 * 50005000 + (rows % 10000) * (rows % 10000 + 1) // 2
    if value != {
        "rows": rows,
        "unique_ids": rows,
        "first_id": 0,
        "last_id": rows - 1,
        "amount_total_cents": expected_sum,
        "invalid_bodies": 0,
    }:
        raise RuntimeError("complete deterministic body oracle failed")


def live_files(entries):
    if any(v["status"] not in {"ADDED", "EXISTING", "DELETED"} for v in entries):
        raise RuntimeError("unknown manifest entry status")
    live = [v for v in entries if v["status"] != "DELETED"]
    if (
        not live
        or any(
            v["content"] != "DATA"
            or v["manifest_content"] != "DATA"
            or v["file_format"].casefold() != "parquet"
            for v in live
        )
        or len({v["file_path"] for v in live}) != len(live)
    ):
        raise RuntimeError("invalid or duplicated live-file metadata")
    return live


def verify(root, rows):
    states = {}
    for stage in ("before", "rolled-back", "committed", "noop", "negative-rolled-back"):
        check_rows(single(root, stage + "-rows"), rows)
        states[stage] = single(root, stage + "-catalog")["metadata"]
    for name in ("inside-transaction", "retained-snapshot"):
        check_rows(single(root, name + "-rows"), rows)
    snapshots = {
        stage: metadata["current-snapshot-id"] for stage, metadata in states.items()
    }
    if (
        snapshots["before"] != snapshots["rolled-back"]
        or snapshots["committed"] == snapshots["before"]
        or snapshots["noop"] != snapshots["committed"]
        or snapshots["negative-rolled-back"] != snapshots["committed"]
        or len({v["table-uuid"] for v in states.values()}) != 1
    ):
        raise RuntimeError(
            "rollback, replace or no-op changed the wrong catalog identity"
        )
    storage = {}
    if any(v["format-version"] != 2 for v in states.values()):
        raise RuntimeError("native maintenance fixture is not Iceberg v2")
    for stage in ("before", "committed"):
        entries = cleanup.load(root / (stage + "-files.json"), root)
        live = live_files(entries)
        parquet = single(root, stage + "-parquet")
        chunks = single(root, stage + "-compression")
        if (
            sum(v["record_count"] for v in live) != rows
            or parquet["rows"] != rows
            or chunks["compressed_bytes"] <= 0
        ):
            raise RuntimeError("native Parquet rows/chunks differ from body oracle")
        storage[stage] = {
            "files": len(live),
            "parquet": parquet,
            "chunks": chunks,
            "encoded_chunk_compression_ratio": chunks["uncompressed_bytes"]
            / chunks["compressed_bytes"],
        }
    if storage["committed"]["files"] >= storage["before"]["files"]:
        raise RuntimeError("native rewrite did not compact the small-file fixture")
    negative = single(root, "negative-body-rows")
    if negative["invalid_bodies"] != 1:
        raise RuntimeError("wrong-body negative control did not exercise the oracle")
    check_rows(negative | {"invalid_bodies": 0}, rows)
    # The CLI emits one JSON array per result; COPY also emits row counts.
    decoder = json.JSONDecoder()
    remaining = (root / "cli-stdout.jsonstream").read_text().strip()
    rewrites = []
    while remaining:
        values, end = decoder.raw_decode(remaining)
        rewrites.extend(v for v in values if "rewritten_data_files_count" in v)
        remaining = remaining[end:].lstrip()
    if len(rewrites) != 3:
        raise RuntimeError("missing native rollback/commit/no-op rewrite results")
    for value in rewrites[:2]:
        if (
            value["rewritten_data_files_count"] != storage["before"]["files"]
            or value["added_data_files_count"] != storage["committed"]["files"]
            or value["failed_data_files_count"]
            or value["removed_delete_files_count"]
            or value["rewritten_bytes_count"] <= 0
        ):
            raise RuntimeError("native rewrite counters differ from committed files")
    if any(rewrites[2].values()):
        raise RuntimeError("repeated maintenance unexpectedly rewrote files")
    if single(root, "native")["engine"] != "v2.0.1":
        raise RuntimeError("unexpected actual CLI engine")
    extensions = cleanup.load(root / "extensions.json", root)
    if {v["extension_name"]: v["extension_version"] for v in extensions} != {
        "avro": "4176191c96",
        "httpfs": "f71cfdb4bf",
        "iceberg": "f979b5b28a",
    } or not all(v["loaded"] for v in extensions):
        raise RuntimeError("loaded native extensions differ")
    profiles = {
        stage: cleanup.load(root / (stage + "-profile.json"), root)
        for stage in ("rollback", "committed", "noop")
    }
    return {
        "snapshots": snapshots,
        "storage": storage,
        "rewrites": rewrites,
        "query_latency_seconds": {
            stage: v["query"]["total_time"] for stage, v in profiles.items()
        },
    }


def independent_readback(root, table, rows, customers, query):
    ddl = "CREATE DATABASE IF NOT EXISTS lake ENGINE=DataLakeCatalog('http://storage:8181', 'demo', 'demo') SETTINGS catalog_type='rest',catalog_credential='demo:demo',vended_credentials=false,warehouse='warehouse',storage_endpoint='http://storage:8333/warehouse'"
    client = [
        "docker",
        "exec",
        query["Id"],
        "clickhouse-client",
        "--password",
        "local-reader",
    ]
    control.call(client + ["--allow_database_iceberg", "1", "--query", ddl])
    sql = f"""SELECT count() AS rows,uniqExact(event_id) AS unique_ids,
min(event_id) AS first_id,max(event_id) AS last_id,sum(amount_cents) AS amount_total_cents,
countIf(event_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL OR payload_sha256 IS NULL
 OR event_id < 0 OR event_id >= {rows} OR customer_id != event_id % {customers}
 OR amount_cents != event_id % 10000 + 1 OR payload_sha256 != lower(hex(SHA256(toString(event_id))))) AS invalid_bodies
FROM lake.`lab.{table}` LIMIT 1 SETTINGS max_execution_time=120,max_memory_usage=805306368,
timeout_before_checking_execution_speed=0,
max_threads=2,input_format_parquet_memory_high_watermark=268435456,
max_rows_to_read=100000000,max_bytes_to_read=17179869184,max_result_rows=1,
result_overflow_mode='throw',output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow"""
    start = time.monotonic_ns()
    control.save_text(root / "clickhouse-query.sql", sql)
    value = json.loads(control.call(client + ["--query", sql]))
    elapsed = time.monotonic_ns() - start
    control.save(root / "clickhouse-rows.json", value)
    check_rows(value, rows)
    return {
        "rows": value,
        "duration_ns": elapsed,
        "scope": "Docker exec, client connection, SQL, result transfer and JSON decode; not query-only latency",
    }


def authoritative_catalog(root, table, storage, checks):
    """Read the native pointer independently of either SQL engine's metadata cache."""
    base = [
        "docker",
        "exec",
        storage["Id"],
        "curl",
        "-q",
        "--noproxy",
        "*",
        "--proto",
        "=http",
        "--connect-timeout",
        "5",
        "--max-time",
        "10",
        "--max-filesize",
        "8388608",
        "--fail",
        "--silent",
        "--show-error",
        "--write-out",
        "\n%{http_code}",
    ]

    def request(path, extra):
        output = control.call(
            base + extra + ["http://127.0.0.1:8181/v1/" + path], timeout=15
        )
        body, _, status = output.rpartition("\n")
        if status != "200" or len(body.encode()) > 8 * 1024**2:
            raise RuntimeError("unexpected native catalog response")
        return json.loads(body)

    token = request(
        "oauth/tokens",
        ["--data", "grant_type=client_credentials&client_id=demo&client_secret=demo"],
    )["access_token"]
    if (
        not isinstance(token, str)
        or not 0 < len(token) <= 65536
        or any(not 32 < ord(c) < 127 for c in token)
    ):
        raise RuntimeError("invalid local OAuth token")
    response = request(
        "namespaces/lab/tables/" + table, ["--header", "Authorization: Bearer " + token]
    )
    control.save(root / "authoritative-catalog.json", response)
    metadata = response["metadata"]
    expected = single(root, "before-catalog")["metadata"]
    if (
        metadata["format-version"] != 2
        or metadata["table-uuid"] != expected["table-uuid"]
        or metadata["current-snapshot-id"] != checks["snapshots"]["committed"]
    ):
        raise RuntimeError("published catalog differs from the native rewrite proof")
    (current,) = [
        s
        for s in metadata["snapshots"]
        if s["snapshot-id"] == metadata["current-snapshot-id"]
    ]
    if (
        current["summary"]["operation"] != "replace"
        or int(current["summary"]["total-data-files"])
        != checks["storage"]["committed"]["files"]
    ):
        raise RuntimeError(
            "native catalog did not publish the expected file replacement"
        )
    return {
        "table_uuid": metadata["table-uuid"],
        "snapshot_id": metadata["current-snapshot-id"],
        "metadata_location": response["metadata-location"],
    }


def finish_cli(root, planned, sampler, originals):
    errors = []
    try:
        value = planned()
        if value is not None:
            if value["State"]["Running"]:
                control.call(
                    ["docker", "stop", "--time", "20", value["Id"]], timeout=30
                )
            value = planned()
            control.save(root / "final.json", control.state(value))
            if value["State"]["Running"] or value["State"]["Pid"]:
                raise RuntimeError("native maintenance fencing is unknown")
    except BaseException as error:
        errors.append("fencing: " + repr(error))
    try:
        _, resources = sampler.close()
        control.save(root / "resource-summary.json", resources)
    except BaseException as error:
        errors.append("resources: " + repr(error))
    for original in originals:
        try:
            if control.live_identity(
                control.owned(original["Id"])
            ) != control.live_identity(original):
                raise RuntimeError("unrelated backend process changed")
        except BaseException as error:
            errors.append("survivor " + original["Id"] + ": " + repr(error))
    return errors


def run(mode="baseline"):
    import duckdb_clickhouse
    import duckdb_conflicts
    import duckdb_rejections
    import duckdb_partial
    import duckdb_crash
    import duckdb_dependencies
    import connector_concurrent
    import connector_read_isolation
    import duckdb_http

    if mode not in {
        "baseline",
        "rejections",
        "conflicts",
        "connector",
        "connector-read",
        "connector-read-isolation",
        "connector-http",
        "connector-http-hot",
        "connector-http-read",
        "connector-partial",
        "connector-crash",
        "connector-dependencies",
        "connector-concurrent",
    }:
        raise ValueError("unknown native maintenance scenario")
    scenario = {
        "rejections": duckdb_rejections,
        "conflicts": duckdb_conflicts,
        "connector": duckdb_clickhouse,
        "connector-read": duckdb_clickhouse,
        "connector-read-isolation": connector_read_isolation,
        "connector-http": duckdb_http,
        "connector-http-hot": duckdb_http,
        "connector-http-read": duckdb_http,
        "connector-partial": duckdb_partial,
        "connector-crash": duckdb_crash,
        "connector-dependencies": duckdb_dependencies,
        "connector-concurrent": connector_concurrent,
    }.get(mode)
    if control.lab.PROFILE != "current":
        raise RuntimeError("DuckDB-native maintenance requires the current profile")
    context = control.local_docker()
    cold_readback.original_plane()  # Reject overlapping example workloads.
    if shutil.disk_usage(control.ROOT).free < 8 * control.GIB:
        raise RuntimeError("native maintenance requires 8 GiB host disk headroom")
    rows, customers, batch = [
        int(os.environ.get(k, default))
        for k, default in (
            ("ROWS", "2000000"),
            ("CUSTOMERS", "10000"),
            ("BATCH_ROWS", "100000"),
        )
    ]
    if mode == "rejections":
        rows, customers, batch = 10, 10, 2
    elif mode == "conflicts":
        rows, customers, batch = (
            duckdb_conflicts.ROWS,
            duckdb_conflicts.CUSTOMERS,
            duckdb_conflicts.APPEND_ROWS,
        )
    run_id = uuid.uuid4()
    table = ("duck_frontend_" if mode == "connector" else "duck_compact_") + run_id.hex
    if mode == "connector":
        artifacts = scenario.artifacts(table, rows, customers, batch)
    elif mode in {"connector-http", "connector-http-hot"}:
        artifacts = scenario.artifacts(
            table, rows, hot_only=mode == "connector-http-hot"
        )
    elif mode in {
        "connector-partial",
        "connector-crash",
        "connector-dependencies",
        "connector-concurrent",
    }:
        artifacts = scenario.artifacts(table, rows)
    elif mode in {"connector-read", "connector-read-isolation", "connector-http-read"}:
        artifacts = scenario.read_artifacts(rows)
    else:
        sql = program(table, rows, customers, batch)
        artifacts = scenario.artifacts(table) if scenario else {"run.sql": sql}
    root = cleanup.regular(
        cleanup.results_root()
        / ("duckdb-" + mode if scenario else "duckdb-maintenance")
        / str(run_id),
        cleanup.results_root(),
    )
    root.mkdir(parents=True)
    for name, contents in artifacts.items():
        control.save_text(root / name, contents)
    storage, query = control.service("storage"), control.service("clickhouse")
    settings = control.current_settings(storage, query)
    # Reserve room for seed, rollback output and committed replacement. This
    # healthy test must not accidentally become a storage-capacity experiment.
    required_slots = 1 if scenario else max(3, (rows * 384 + 255999999) // 256000000)
    if mode in {"connector", "connector-http", "connector-dependencies"}:
        required_slots = (rows * 128 + 255999999) // 256000000 + 1
        if mode == "connector-dependencies" and scenario.seed_case(rows) is not None:
            required_slots = 1  # Existing 2M-row source is read-only; only 3k probe rows are new S3 output.
    elif mode in {
        "connector-read",
        "connector-read-isolation",
        "connector-http-read",
        "connector-http-hot",
        "connector-partial",
        "connector-crash",
    }:
        required_slots = 0
    free_slots = settings["topology"]["Topology"].get("Free")
    if type(free_slots) is not int or free_slots < required_slots:
        raise RuntimeError(
            "insufficient native volume slots for retained rewrite outputs"
        )
    docker_df = control.call(["docker", "exec", storage["Id"], "df", "-Pk", "/data"])
    if int(docker_df.splitlines()[-1].split()[3]) * 1024 < 8 * control.GIB:
        raise RuntimeError("native maintenance requires 8 GiB Docker disk headroom")
    (image,) = json.loads(
        control.call(
            [
                "docker",
                "image",
                "inspect",
                duckdb_http.IMAGE
                if mode.startswith("connector-http")
                else control.lab.GO_IMAGE,
            ]
        )
    )
    if image["Config"].get("Labels", {}).get("org.examples.owner") != control.OWNER:
        raise RuntimeError("native CLI image lacks repository ownership")
    if mode in {
        "connector",
        "connector-read",
        "connector-read-isolation",
        "connector-partial",
        "connector-crash",
        "connector-dependencies",
        "connector-concurrent",
    }:
        duckdb_clickhouse.prepare(root, image)
    if mode == "connector-partial":
        control.save(
            root / "intents.json",
            {
                "table": table,
                "rows_per_attempt": rows,
                "attempts": [1, 2],
                "corrected_replays": [1, 2],
                "corrected_value": 1,
                "replay_semantics": "immutable event_id, ReplacingMergeTree with FINAL reads",
                "invalid_last_value": 128,
                "recovery_marker": rows,
                "failed_response_does_not_imply_rollback": True,
            },
        )
    frozen = control.sources()
    for relative, expected_sha in frozen.items():
        data = (control.ROOT / relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_sha:
            raise RuntimeError("native-maintenance source changed before preservation")
        destination = root / "source" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        control.save_text(destination, data.decode())
    control.save(
        root / "invocation.json",
        {
            "context": context,
            "table": table,
            "rows": rows,
            "mode": mode,
            "customers": customers,
            "batch_rows": batch,
            "source_sha256": frozen,
            "native_settings": settings,
            "image_id": image["Id"],
            "docker_df": docker_df,
            "started_ns": time.time_ns(),
        },
    )
    env = control.DOCKER_ENV | {"RESULTS_DIR": str(root.relative_to(control.ROOT))}
    name = "iceberg-duckdb-native-" + run_id.hex
    sampler = ResourceSampler(root, env=control.DOCKER_ENV)
    sampler.bind("storage", storage["Id"], "/data")
    sampler.bind("clickhouse", query["Id"], "/var/lib/clickhouse")
    sampler.start()
    failure, result, identifier = None, None, None

    def planned():
        ids = control.call(
            [
                "docker",
                "ps",
                "--all",
                "--no-trunc",
                "-q",
                "--filter",
                "name=^/" + name + "$",
            ]
        ).split()
        if not ids:
            return None
        if len(ids) != 1:
            raise RuntimeError("ambiguous native maintenance owner")
        value = control.owned(ids[0], "runner")
        if value["Name"] != "/" + name:
            raise RuntimeError("native maintenance name differs")
        return value

    try:
        if planned() is not None:
            raise RuntimeError("planned native maintenance already exists")
        control.save(
            root / "planned.json",
            {"name": name, "project": control.PROJECT, "service": "runner"},
        )
        identifier = control.call(
            (
                control.COMPOSE + ["-f", "docker-compose.http.yml"]
                if mode.startswith("connector-http")
                else control.COMPOSE
            )
            + [
                "run",
                "-d",
                "--name",
                name,
                "--no-deps",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--entrypoint",
                "/bin/sh",
                "runner",
                "-c",
                "while ! test -f /results/release; do sleep .1; done; exec "
                + (
                    "/bin/sh /results/run.sh"
                    if scenario
                    else "duckdb -batch -bail -json :memory: < /results/run.sql"
                ),
            ],
            env=env,
        ).strip()
        value = planned()
        if value is None or value["Id"] != identifier or value["Image"] != image["Id"]:
            raise RuntimeError("created native workload identity differs")
        control.check_caps(value, "recovery-compactor")  # Same 2 CPU / 2 GiB budget.
        control.check_mounts(value, root)
        networks = {
            n["NetworkID"] for n in storage["NetworkSettings"]["Networks"].values()
        }
        control.check_networks(value, networks)
        if value["Config"].get("Labels", {}).get("org.examples.owner") != control.OWNER:
            raise RuntimeError("native runner lacks repository ownership")
        hashes = control.call(
            [
                "docker",
                "exec",
                identifier,
                "/bin/sh",
                "-c",
                "sha256sum /usr/local/bin/duckdb /opt/duckdb/extensions/v2.0.1/linux_arm64/*.duckdb_extension",
            ]
        )
        control.save_text(root / "native-sha256.txt", hashes)
        expected = {
            "be6f927c2bcf25fe4da383bc2e276fcd91a74be8da217704685d5a0910a7b7ed",
            "d8bb1c0ecb28ca10c6be7d293f1518875bd1f0054beda8b5ffdde3b4436cbac3",
            "91981c5b2091ae22179232643862f6929031b7f911a5aea075d8df1e1c4a3c60",
        }
        if {
            line.split()[0]
            for line in hashes.splitlines()
            if line.endswith(".duckdb_extension")
        } != expected:
            raise RuntimeError("native extension binary hashes differ")
        control.save(root / "started.json", control.state(value))
        sampler.bind("duckdb-cli", identifier, "/results")
        control.wait_coverage(
            sampler, ["storage", "clickhouse", "duckdb-cli"], time.time_ns()
        )
        if mode in {"connector", "connector-http"}:
            scenario.setup(root, table, storage)
        control.save_text(root / "release", "release\n")
        if mode in {
            "connector-crash",
            "connector-dependencies",
            "connector-concurrent",
        }:
            query = scenario.drive(root, table, rows, identifier, query, sampler)
            control.save_text(root / "native-complete", "complete\n")
        if scenario:
            deadline = time.monotonic() + 120
            while not (root / "native-complete").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("native maintenance sequence timed out")
                if not planned()["State"]["Running"]:
                    break  # Retain the ordinary exit/log diagnostics below.
                time.sleep(0.1)
            if (root / "native-complete").exists():
                control.wait_coverage(sampler, ["duckdb-cli"], time.time_ns())
                control.save_text(root / "native-finish", "finish\n")
        control.call(["docker", "wait", identifier], timeout=600)
        value = planned()
        control.save(root / "ended.json", control.state(value))
        logs = subprocess.run(
            ["docker", "logs", identifier],
            env=control.DOCKER_ENV,
            capture_output=True,
            text=True,
            timeout=20,
        )
        control.save_text(root / "cli-stdout.jsonstream", logs.stdout)
        control.save_text(root / "cli-stderr.log", logs.stderr)
        if logs.returncode:
            raise RuntimeError("native CLI logs could not be retained")
        cold_storage.stopped(value)
        if mode in {
            "connector-partial",
            "connector-crash",
            "connector-dependencies",
            "connector-concurrent",
        }:
            result = scenario.verify(root, table, rows, query)
        elif mode in {
            "connector-read",
            "connector-read-isolation",
            "connector-http-read",
        }:
            result = scenario.verify_read(root, rows)
        else:
            result = scenario.verify(root, rows) if scenario else verify(root, rows)
            if mode == "connector-http-hot":
                result["native_hot_readback"] = scenario.independent_hot(
                    root, table, rows, query
                )
            else:
                result["clickhouse_readback"] = independent_readback(
                    root, table, rows, customers, query
                )
        if mode in {"connector", "connector-http"}:
            result["published_catalog"] = scenario.finalize(root, table, storage, rows)
        if not scenario:
            result["authoritative_catalog"] = authoritative_catalog(
                root, table, storage, result
            )
        if control.sources() != frozen:
            raise RuntimeError("native-maintenance source changed during execution")
    except BaseException as error:
        failure = error
    finally:
        cleanup_errors = finish_cli(root, planned, sampler, (storage, query))
        if cleanup_errors and failure is None:
            failure = RuntimeError("; ".join(cleanup_errors))
        control.save(
            root / "result.json",
            {
                "passed": failure is None and result is not None,
                "error": repr(failure) if failure else None,
                "checks": result,
                "cleanup_errors": cleanup_errors,
                "completed_ns": time.time_ns(),
                "scope": (
                    "Independent local S3/catalog TCP rejection with live ordinary DuckDB hot reads/writes and native pass-through cold diagnostics; no capacity or complete architecture qualification."
                    if mode == "connector-dependencies"
                    else "Retained-volume ClickHouse SIGKILL with independent native DuckDB requests and external witness; no capacity or complete production qualification."
                    if mode == "connector-crash"
                    else "Read-only generated-row connector regression; no persisted fixture, independent engine export or capacity qualification."
                    if mode == "connector-read"
                    else "Native "
                    + mode
                    + " controls; independent body readback; no live-fault or performance qualification."
                    if scenario
                    else "Healthy native transaction/compaction and independent-engine body checks only. Live readers/writers and faults remain separate qualification gates."
                ),
            },
        )
    print(root.relative_to(control.ROOT), flush=True)
    if failure:
        raise failure


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--rejections", dest="mode", action="store_const", const="rejections"
    )
    group.add_argument(
        "--conflicts", dest="mode", action="store_const", const="conflicts"
    )
    group.add_argument(
        "--connector", dest="mode", action="store_const", const="connector"
    )
    group.add_argument(
        "--connector-read", dest="mode", action="store_const", const="connector-read"
    )
    group.add_argument(
        "--connector-partial",
        dest="mode",
        action="store_const",
        const="connector-partial",
    )
    group.add_argument(
        "--connector-crash", dest="mode", action="store_const", const="connector-crash"
    )
    group.add_argument(
        "--connector-dependencies",
        dest="mode",
        action="store_const",
        const="connector-dependencies",
    )
    group.add_argument(
        "--connector-concurrent",
        dest="mode",
        action="store_const",
        const="connector-concurrent",
    )
    group.add_argument(
        "--connector-read-isolation",
        dest="mode",
        action="store_const",
        const="connector-read-isolation",
    )
    group.add_argument(
        "--connector-http", dest="mode", action="store_const", const="connector-http"
    )
    group.add_argument(
        "--connector-http-read",
        dest="mode",
        action="store_const",
        const="connector-http-read",
    )
    group.add_argument(
        "--connector-http-hot",
        dest="mode",
        action="store_const",
        const="connector-http-hot",
    )
    parser.set_defaults(mode="baseline")
    args = parser.parse_args()
    with cold_storage.controller_lock() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(mode=args.mode)
