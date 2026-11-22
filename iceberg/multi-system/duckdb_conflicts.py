"""Controlled append between native rewrite staging and COMMIT."""

import json
import re

import duckdb_maintenance as maintenance
from duckdb_rejections import connection_sql


SEED_ROWS = 100000
APPEND_ROWS = 10000
ROWS = SEED_ROWS + APPEND_ROWS
CUSTOMERS = 10000


def artifacts(table):
    if not re.fullmatch(r"duck_compact_[0-9a-f]{32}", table):
        raise ValueError("fresh owned table identifier required")
    qualified = "lake.lab." + table
    connect = connection_sql()

    def copy(query, name):
        return f"COPY ({query}) TO '/results/{name}.json' (FORMAT JSON, ARRAY true);\n"

    def state(name, rows):
        return copy(
            maintenance.oracle_query(qualified, rows, CUSTOMERS), name + "-rows"
        ) + copy(
            f"SELECT metadata_location,metadata::JSON AS metadata FROM iceberg_load_table_response('{qualified}')",
            name + "-catalog",
        )

    seed = (
        connect
        + f"CREATE SCHEMA IF NOT EXISTS lake.lab; CREATE TABLE {qualified} (event_id BIGINT,customer_id INTEGER,amount_cents BIGINT,payload_sha256 VARCHAR) WITH ('format-version'='2');\n"
    )
    for lo in range(0, SEED_ROWS, APPEND_ROWS):
        seed += f"INSERT INTO {qualified} SELECT i,CAST(i%{CUSTOMERS} AS INTEGER),i%10000+1,sha256(CAST(i AS VARCHAR)) FROM range({lo},{lo + APPEND_ROWS}) t(i);\n"
        seed += copy(
            f"SELECT {lo} AS first_id,{lo + APPEND_ROWS} AS end_id", f"ack-{lo}"
        )
    seed += state("before", SEED_ROWS)
    seed += copy(
        "SELECT name,value FROM duckdb_settings() WHERE name IN ('threads','memory_limit','max_temp_directory_size','http_timeout','http_retries','http_retry_wait_ms','http_retry_backoff','http_keep_alive') ORDER BY name",
        "client-settings",
    )
    rewrite = f"CALL iceberg_rewrite_data_files('{qualified}',target_file_size_bytes=>134217728,min_input_files=>2);\n"
    staged = connect + "BEGIN;\n" + rewrite + state("staged", SEED_ROWS)
    staged += copy("SELECT true AS staged", "stage-ready")
    append = (
        connect
        + f"INSERT INTO {qualified} SELECT i,CAST(i%{CUSTOMERS} AS INTEGER),i%10000+1,sha256(CAST(i AS VARCHAR)) FROM range({SEED_ROWS},{ROWS}) t(i);\n"
    )
    append += copy(f"SELECT {SEED_ROWS} AS first_id,{ROWS} AS end_id", "ack-append")
    append += state("appended", ROWS)
    # A separate process checks the published state while the staged rewrite's
    # transaction remains open; no writer is stopped to force the stale commit.
    observer = connect + state("observer", ROWS)
    after = connect + state("after-conflict", ROWS)
    retry = connect + rewrite + state("replanned", ROWS)
    retry += f"SET VARIABLE conflict_files=(SELECT list(file_path) FROM iceberg_metadata('{qualified}') WHERE status <> 'DELETED' AND content='DATA');\n"
    retry += copy(
        "SELECT file_name,file_size_bytes,num_rows,footer_size FROM parquet_file_metadata(getvariable('conflict_files'))",
        "data-files",
    )
    retry += copy(
        "SELECT count(*) AS column_chunks,sum(total_compressed_size) AS compressed_bytes,sum(total_uncompressed_size) AS uncompressed_bytes FROM parquet_metadata(getvariable('conflict_files'))",
        "compression",
    )
    shell = """#!/bin/sh
set -u
run_cli() {
  duckdb -batch -bail -json :memory: < "/results/$1.sql" > "/results/$1-stdout.log" 2> "/results/$1-stderr.log"
  native_code=$?
  printf '%s\\n' "$native_code" > "/results/$1-exit.txt"
  return "$native_code"
}
run_cli seed || exit 1
(
  {
    cat /results/stage.sql
    # The host controller also bounds the complete sequence to 120 seconds.
    count=0
    while ! test -f /results/commit-release; do
      count=$((count+1)); test "$count" -lt 600 || exit 1
      sleep .1
    done
    printf 'COMMIT;\\n'
  } | duckdb -batch -bail -json :memory: > /results/rewrite-stdout.log 2> /results/rewrite-stderr.log
  native_code=$?
  printf '%s\\n' "$native_code" > /results/rewrite-exit.txt
) &
rewrite_pid=$!
count=0
while ! test -f /results/stage-ready.json; do
  test ! -f /results/rewrite-exit.txt || exit 1
  count=$((count+1)); test "$count" -lt 600 || exit 1
  sleep .1
done
run_cli append || exit 1
run_cli observer || exit 1
printf 'commit\\n' > /results/commit-release
wait "$rewrite_pid" || exit 1
# A definite conflict must reach the native client, rather than silently losing
# the append or pretending the stale rewrite committed.
test "$(cat /results/rewrite-exit.txt)" -eq 1 || exit 1
run_cli after || exit 1
run_cli replan || exit 1
printf 'complete\\n' > /results/native-complete
while ! test -f /results/native-finish; do sleep .1; done
"""
    return {
        "seed.sql": seed,
        "stage.sql": staged,
        "append.sql": append,
        "observer.sql": observer,
        "after.sql": after,
        "replan.sql": retry,
        "run.sh": shell,
    }


def verify(root, rows):
    if rows != ROWS:
        raise RuntimeError("unexpected conflict fixture size")
    for name in ("seed", "append", "observer", "after", "replan", "rewrite"):
        expected = 1 if name == "rewrite" else 0
        if (root / (name + "-exit.txt")).read_text().strip() != str(expected):
            raise RuntimeError("unexpected native CLI outcome: " + name)
    error = (root / "rewrite-stderr.log").read_text()
    if not ("409" in error and "conflict" in error.lower()):
        raise RuntimeError("stale rewrite did not report the expected catalog conflict")
    counts = {}
    for name, expected_files in (
        ("rewrite", SEED_ROWS // APPEND_ROWS),
        ("replan", SEED_ROWS // APPEND_ROWS + 1),
    ):
        remaining = (root / (name + "-stdout.log")).read_text().strip()
        values = []
        while remaining:
            records, end = json.JSONDecoder().raw_decode(remaining)
            values += [v for v in records if "rewritten_data_files_count" in v]
            remaining = remaining[end:].lstrip()
        if (
            len(values) != 1
            or values[0]["rewritten_data_files_count"] != expected_files
            or values[0]["added_data_files_count"] != 1
            or values[0]["failed_data_files_count"]
        ):
            raise RuntimeError(
                "native rewrite did not stage the expected file replacement"
            )
        counts[name] = values[0]
    states = {}
    for phase in (
        "before",
        "staged",
        "appended",
        "observer",
        "after-conflict",
        "replanned",
    ):
        maintenance.check_rows(
            maintenance.single(root, phase + "-rows"),
            SEED_ROWS if phase in {"before", "staged"} else ROWS,
        )
        states[phase] = maintenance.single(root, phase + "-catalog")
    if (
        states["appended"] != states["observer"]
        or states["appended"] != states["after-conflict"]
    ):
        raise RuntimeError("stale rewrite changed the append's published state")
    ids = {k: v["metadata"]["current-snapshot-id"] for k, v in states.items()}
    if (
        'branch "main" has changed' not in error
        or f"expected id {ids['before']}, found {ids['appended']}" not in error
    ):
        raise RuntimeError("conflict did not identify the actual competing snapshots")
    for lo in range(0, SEED_ROWS, APPEND_ROWS):
        if maintenance.single(root, f"ack-{lo}") != {
            "first_id": lo,
            "end_id": lo + APPEND_ROWS,
        }:
            raise RuntimeError("missing or changed acknowledged seed interval")
    if maintenance.single(root, "ack-append") != {
        "first_id": SEED_ROWS,
        "end_id": ROWS,
    }:
        raise RuntimeError("missing or changed acknowledged append interval")
    if ids["before"] == ids["appended"] or ids["appended"] == ids["replanned"]:
        raise RuntimeError("append and replanned rewrite must publish new snapshots")
    if len({v["metadata"]["table-uuid"] for v in states.values()}) != 1:
        raise RuntimeError("conflict test changed table identity")
    return {
        "seed_rows": SEED_ROWS,
        "acknowledged_append_rows": APPEND_ROWS,
        "rows_verified": ROWS,
        "snapshots": ids,
        "native_rewrite_counts": counts,
        "stale_rewrite_rejected": True,
        "append_preserved": True,
        "replanned_rewrite_passed": True,
        "production_qualified": False,
    }
