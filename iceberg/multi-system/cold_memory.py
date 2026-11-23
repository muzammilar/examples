"""Read-only native ClickHouse isolation of a retained concurrent-case cold table."""

import fcntl
import hashlib
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

import cold_readback
import cold_storage
import connector_concurrent as concurrent
import duckdb_dependencies as dependencies
import recovery_cleanup as cleanup
import recovery_control as control
from recovery_resources import ResourceSampler


VARIANTS = {
    "baseline": "",
    "watermark64": ",input_format_parquet_memory_high_watermark=67108864",
    "watermark64-thread1": ",input_format_parquet_memory_high_watermark=67108864,max_threads=1",
}
BLOCK_VARIANTS = {
    "watermark64": VARIANTS["watermark64"],
    "watermark64-block8192": VARIANTS["watermark64"]
    + ",input_format_parquet_max_block_size=8192",
    "watermark64-block8192-thread1": VARIANTS["watermark64-thread1"]
    + ",input_format_parquet_max_block_size=8192",
}


def run(identifier, blocks=False):
    variants = BLOCK_VARIANTS if blocks else VARIANTS
    case_id = uuid.UUID(identifier)
    if identifier != str(case_id) or control.lab.PROFILE != "current":
        raise ValueError("canonical current-profile concurrent case required")
    context = control.local_docker()
    cold_readback.original_plane()
    case = cleanup.regular(
        cleanup.results_root() / "duckdb-connector-concurrent" / identifier,
        cleanup.results_root(),
    )
    config = cleanup.load(case / "concurrent-input.json", case)
    table = config["table"]
    if not re.fullmatch(r"duck_compact_[0-9a-f]{32}", table):
        raise ValueError("unexpected owned cold table name")
    history = cleanup.load(case / "cold-history.json", case)
    if (
        config["seed_rows"] != 2000000
        or not history
        or any(
            v["status"] != "ok" or v["end"] - v["first"] != concurrent.COLD_BATCH
            for v in history
        )
    ):
        raise RuntimeError(
            "this diagnostic requires complete acknowledged cold batches"
        )
    intervals = sorted((v["first"], v["end"]) for v in history)
    rows = config["seed_rows"] + len(history) * concurrent.COLD_BATCH
    if intervals != [
        (i, i + concurrent.COLD_BATCH)
        for i in range(config["seed_rows"], rows, concurrent.COLD_BATCH)
    ]:
        raise RuntimeError("cold witness has gaps, duplicates or overlaps")
    query, storage = control.service("clickhouse"), control.service("storage")
    root = case / "memory-diagnostics" / str(uuid.uuid4())
    root.mkdir(parents=True)
    source = Path(__file__).read_bytes()
    control.save_text(root / "source.py", source.decode())
    control.save(
        root / "started.json",
        {
            "environment": context,
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "source_case": identifier,
            "table": table,
            "expected_rows": rows,
            "variants": variants,
            "query": control.state(query),
            "storage": control.state(storage),
            "scope": "Sequential native ClickHouse reads, no DuckDB application or live writers; caches uncontrolled and no capacity claim.",
        },
    )
    before = dependencies.catalog(storage, table)
    control.save(root / "catalog-before.json", before)
    if before["metadata"]["table-uuid"] != config["table_uuid"]:
        raise RuntimeError("native table UUID differs from the witness")
    client = [
        "docker",
        "exec",
        query["Id"],
        "clickhouse-client",
        "--password",
        "local-reader",
        "--query",
    ]
    settings = "SELECT name,value,description FROM system.settings WHERE name IN ('max_threads','input_format_parquet_memory_high_watermark','input_format_parquet_max_block_size') ORDER BY name LIMIT 10 SETTINGS max_execution_time=5,max_rows_to_read=10000,max_memory_usage=67108864 FORMAT JSONEachRow"
    control.save_text(root / "settings.jsonl", control.call(client + [settings]))
    sampler = ResourceSampler(root, env=control.DOCKER_ENV)
    sampler.bind("clickhouse", query["Id"], "/var/lib/clickhouse")
    sampler.bind("storage", storage["Id"], "/data")
    sampler.start()
    results, error = [], None
    try:
        # Rotate order instead of always giving a variant the first-cache penalty.
        names = list(variants)
        for trial in range(3):
            for name in names[trial:] + names[:trial]:
                stem = f"trial-{trial + 1}-{name}"
                sql = (
                    concurrent.read_oracle(
                        table,
                        rows,
                        config["seed_rows"],
                        [v["first"] // concurrent.COLD_BATCH for v in history],
                    )
                    + variants[name]
                    + ",result_overflow_mode='throw',max_result_rows=1,output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow"
                )
                control.save_text(root / (stem + ".sql"), sql)
                start = time.monotonic_ns()
                completed = subprocess.run(
                    client + [sql],
                    env=control.DOCKER_ENV,
                    capture_output=True,
                    text=True,
                    timeout=25,
                )
                elapsed = time.monotonic_ns() - start
                control.save_text(root / (stem + "-stdout.jsonl"), completed.stdout)
                control.save_text(root / (stem + "-stderr.log"), completed.stderr)
                record = {
                    "trial": trial + 1,
                    "variant": name,
                    "exit_code": completed.returncode,
                    "duration_ns": elapsed,
                    "status": "fail",
                    "body": None,
                }
                if completed.returncode == 0:
                    body = json.loads(completed.stdout)
                    concurrent.check_read(body, rows, rows)
                    record.update(status="ok", body=body)
                control.save(root / (stem + ".json"), record)
                results.append(record)
        after = dependencies.catalog(storage, table)
        control.save(root / "catalog-after.json", after)
        if (before["metadata-location"], before["metadata"]["current-snapshot-id"]) != (
            after["metadata-location"],
            after["metadata"]["current-snapshot-id"],
        ):
            raise RuntimeError("table changed during the read-only diagnostic")
        for role, original in (("clickhouse", query), ("storage", storage)):
            if control.live_identity(control.service(role)) != control.live_identity(
                original
            ):
                raise RuntimeError("service restarted during isolation")
    except BaseException as failure:
        error = failure
    finally:
        try:
            _, resources = sampler.close()
            control.save(root / "resource-summary.json", resources)
        except BaseException as failure:
            error = error or failure
        control.save(
            root / "result.json",
            {
                "completed": error is None,
                "error": repr(error) if error else None,
                "requests": results,
                "capacity_qualified": False,
                "concurrent_recovery_qualified": False,
                "compression": "No rewrite or codec change. New footer measurement unavailable in this native-read isolation; earlier snapshot codec audits remain separate.",
            },
        )
    print(root.relative_to(control.ROOT), flush=True)
    if error:
        raise error


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case")
    parser.add_argument(
        "--blocks", action="store_true", help="Compare bounded Parquet output blocks"
    )
    args = parser.parse_args()
    with cold_storage.controller_lock() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args.case, args.blocks)
