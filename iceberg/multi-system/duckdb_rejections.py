"""Native maintenance rejection probes; each failing CLI owns its transaction."""

import re

import duckdb_maintenance as maintenance


ERRORS = {
    "pending-write": "has uncommitted changes in this transaction; commit them first",
    "pending-schema": "has uncommitted changes in this transaction; commit them first",
    "v3": "iceberg_rewrite_data_files does not yet support V3 tables",
}


def artifacts(table):
    if not re.fullmatch(r"duck_compact_[0-9a-f]{32}", table):
        raise ValueError("fresh owned table identifier required")
    qualified = "lake.lab." + table
    v3 = qualified + "_v3"
    connect = """SET threads=2; SET memory_limit='1GB';
SET temp_directory='/results/spill'; SET max_temp_directory_size='1GB';
SET extension_directories=['/opt/duckdb/extensions']; LOAD httpfs; LOAD avro; LOAD iceberg;
SET http_timeout=30; SET http_retries=3; SET http_retry_wait_ms=100; SET http_retry_backoff=4;
CREATE SECRET local_s3 (TYPE S3, KEY_ID 'demo', SECRET 'demo', ENDPOINT 'storage:8333', URL_STYLE 'path', USE_SSL false);
CREATE SECRET local_catalog (TYPE ICEBERG, URI 'http://storage:8181', CLIENT_ID 'demo', CLIENT_SECRET 'demo');
ATTACH 's3://warehouse' AS lake (TYPE iceberg, SECRET local_catalog, URI 'http://storage:8181', READ_ONLY false);
SELECT CASE WHEN version()='v2.0.1' THEN true ELSE error('require native v2.0.1') END;
"""

    def copy(query, name):
        return f"COPY ({query}) TO '/results/{name}.json' (FORMAT JSON, ARRAY true);\n"

    def catalog(target, name):
        return copy(
            f"SELECT metadata_location,metadata::JSON AS metadata FROM iceberg_load_table_response('{target}')",
            name,
        )

    schema = "(event_id BIGINT,customer_id INTEGER,amount_cents BIGINT,payload_sha256 VARCHAR)"
    seed = (
        connect
        + f"CREATE SCHEMA IF NOT EXISTS lake.lab; CREATE TABLE {qualified} {schema} WITH ('format-version'='2');\n"
    )
    seed += f"INSERT INTO {qualified} SELECT i,CAST(i%10 AS INTEGER),i+1,sha256(CAST(i AS VARCHAR)) FROM range(10) t(i);\n"
    seed += f"CREATE TABLE {v3} {schema} WITH ('format-version'='3');\n"
    seed += copy(maintenance.oracle_query(qualified, 10, 10), "before-rows")
    seed += catalog(qualified, "before-catalog") + catalog(v3, "v3-before-catalog")
    seed += f"SET VARIABLE rejection_files=(SELECT list(file_path) FROM iceberg_metadata('{qualified}') WHERE status <> 'DELETED' AND content='DATA');\n"
    seed += copy(
        "SELECT count(*) AS column_chunks,sum(total_compressed_size) AS compressed_bytes,sum(total_uncompressed_size) AS uncompressed_bytes FROM parquet_metadata(getvariable('rejection_files'))",
        "compression",
    )
    seed += copy(
        "SELECT file_name,file_size_bytes,num_rows,footer_size FROM parquet_file_metadata(getvariable('rejection_files'))",
        "data-files",
    )
    seed += copy(
        "SELECT name,value FROM duckdb_settings() WHERE name IN ('threads','memory_limit','max_temp_directory_size','http_timeout','http_retries','http_retry_wait_ms','http_retry_backoff','http_keep_alive') ORDER BY name",
        "client-settings",
    )
    statements = {
        "pending-write": f"BEGIN; INSERT INTO {qualified} VALUES (10,0,11,sha256('10'));",
        "pending-schema": f"BEGIN; ALTER TABLE {qualified} ADD COLUMN rejected_column INTEGER;",
        "v3": "",
    }
    result = {"seed.sql": seed}
    for name, statement in statements.items():
        target = v3 if name == "v3" else qualified
        result[name + ".sql"] = (
            connect + statement + f"\nCALL iceberg_rewrite_data_files('{target}');\n"
        )
    verify = connect + copy(maintenance.oracle_query(qualified, 10, 10), "after-rows")
    verify += catalog(qualified, "after-catalog") + catalog(v3, "v3-after-catalog")
    verify += copy(
        f"SELECT column_name,data_type FROM information_schema.columns WHERE table_catalog='lake' AND table_schema='lab' AND table_name='{table}' ORDER BY ordinal_position",
        "after-schema",
    )
    verify += copy(f"SELECT count(*) AS rows FROM {v3}", "v3-after-rows")
    result["verify.sql"] = verify
    shell = ["#!/bin/sh", "set -u"]
    for name in ("seed", *ERRORS, "verify"):
        expected = 1 if name in ERRORS else 0
        shell += [
            f"duckdb -batch -bail -json :memory: < /results/{name}.sql > /results/{name}-stdout.log 2> /results/{name}-stderr.log",
            "native_code=$?",
            f'printf "%s\\n" "$native_code" > /results/{name}-exit.txt',
            f'[ "$native_code" -eq {expected} ] || exit 1',
        ]
    # Keep the cgroup alive after the short CLI children exit so the sampler
    # captures their cumulative CPU and peak memory rather than only shell idle.
    shell += [
        "printf 'complete\\n' > /results/native-complete",
        "while ! test -f /results/native-finish; do sleep .1; done",
    ]
    result["run.sh"] = "\n".join(shell) + "\n"
    return result


def verify(root, rows):
    if rows != 10:
        raise RuntimeError("rejection fixture must remain explicitly ten rows")
    for name in ("seed", *ERRORS, "verify"):
        expected = 1 if name in ERRORS else 0
        if (root / (name + "-exit.txt")).read_text().strip() != str(expected):
            raise RuntimeError("unexpected native CLI outcome: " + name)
        stderr = (root / (name + "-stderr.log")).read_text()
        if name in ERRORS and (
            stderr.count("Not implemented Error:") != 1 or ERRORS[name] not in stderr
        ):
            raise RuntimeError("wrong native rejection: " + name)
    for phase in ("before", "after"):
        maintenance.check_rows(maintenance.single(root, phase + "-rows"), 10)
    for prefix in ("", "v3-"):
        before = maintenance.single(root, prefix + "before-catalog")
        after = maintenance.single(root, prefix + "after-catalog")
        if before != after:
            raise RuntimeError("rejected maintenance changed committed metadata")
        if prefix and before["metadata"]["format-version"] != 3:
            raise RuntimeError("v3 rejection did not use a native v3 table")
    schema = maintenance.cleanup.load(root / "after-schema.json", root)
    if [(v["column_name"], v["data_type"]) for v in schema] != [
        ("event_id", "BIGINT"),
        ("customer_id", "INTEGER"),
        ("amount_cents", "BIGINT"),
        ("payload_sha256", "VARCHAR"),
    ]:
        raise RuntimeError("pending schema change escaped its failed transaction")
    if maintenance.single(root, "v3-after-rows") != {"rows": 0}:
        raise RuntimeError("v3 rejection changed the empty table")
    return {
        "expected_rejections": list(ERRORS),
        "rows_verified": 10,
        "metadata_unchanged": True,
        "pending_data_and_schema_rolled_back": True,
        "production_qualified": False,
    }
