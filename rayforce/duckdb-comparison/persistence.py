"""Persistence capability gates; no live recovery or capacity claims yet."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import struct
import time
import uuid

import experiment as bench
import ingestion


def encoded_headers(storage, rows):
    """Inspect pinned col.h/encfmt.h metadata; the engine verifies actual data."""
    output = []
    for table, columns, count in [
        ("trades", ["id", "customer", "symbol", "day", "qty", "price"], rows),
        ("customers", ["customer", "region", "factor"], 10000),
    ]:
        for column in columns:
            path = storage / table / column
            with path.open("rb") as stream:
                header = stream.read(96)
            if (
                len(header) < 96
                or header[17] != 2
                or header[18] != 5
                or header[32:36] != b"RENC"
                or struct.unpack_from("<q", header, 24)[0] != count
            ):
                raise RuntimeError(
                    "unexpected pinned encoded I64 column header: " + str(path)
                )
            output.append(
                {
                    "table": table,
                    "column": column,
                    "rows": count,
                    "format_generation": 2,
                    "payload_magic": "RENC",
                    "raw_i64_value_bytes": count * 8,
                    "file_bytes": path.stat().st_size,
                }
            )
    return output


def run_probe(full_snapshot=False, engine="duckdb"):
    started = time.perf_counter_ns()
    before = resource.getrusage(resource.RUSAGE_SELF)
    children_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    bench.RESULTS.mkdir(exist_ok=True)
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        root = bench.RESULTS / "persistence" / uuid.uuid4().hex
        storage = root / "storage"
        storage.mkdir(parents=True)
        plans = bench.RESULTS / "plans"
        plans.mkdir(exist_ok=True)
        plan = plans / (engine + "-" + root.name + ".tsv")
        info = json.loads(
            bench.capture(["docker", "info", "--format", "{{json .}}"], text=True)
        )
        environment = {
            "host": list(os.uname()),
            "docker": {
                k: info[k]
                for k in (
                    "ServerVersion",
                    "KernelVersion",
                    "Architecture",
                    "NCPU",
                    "MemTotal",
                    "OperatingSystem",
                )
            },
            "storage": "Owned host directory bind-mounted into one Docker VM; physical medium/IOPS not qualified",
            "topology": "Two serial independent processes on one host; no HA or power-loss proof",
        }
        if os.uname().sysname == "Darwin":
            environment["cpu_model"] = bench.capture(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
        ingestion.save(root / "environment.json", environment)
        expected = b"id,body\n1,7\n2,14\n"
        phases = [
            [
                (
                    "load",
                    "schema",
                    "",
                    "CREATE TABLE durable_probe(id BIGINT PRIMARY KEY, body BIGINT NOT NULL)",
                ),
                (
                    "write",
                    "insert",
                    "",
                    "INSERT INTO durable_probe VALUES (1,7),(2,14)",
                ),
                (
                    "verify",
                    "body",
                    "1,7;2,14;",
                    "SELECT id,body FROM durable_probe ORDER BY id",
                ),
            ],
            [
                (
                    "verify",
                    "reopened-body",
                    "1,7;2,14;",
                    "SELECT id,body FROM durable_probe ORDER BY id",
                ),
                (
                    "export",
                    "body-export",
                    "",
                    "COPY (SELECT id,body FROM durable_probe ORDER BY id) TO '/verification/body.csv' (HEADER TRUE)",
                ),
                (
                    "export",
                    "native-compression",
                    "",
                    "COPY (SELECT * FROM pragma_storage_info('durable_probe')) TO '/verification/storage-info.csv' (HEADER TRUE)",
                ),
            ],
        ]
        fixture = None
        expected_export = {
            "rows": 2,
            "bytes": len(expected),
            "sha256": hashlib.sha256(expected).hexdigest(),
        }
        if full_snapshot:
            fixture = bench.prepare()
            loads, queries = bench.queries(engine)
            answers = json.loads((bench.RESULTS / "data/expected.json").read_text())
            checks = [
                ("verify", name, answers[name], query)
                for name, query in queries.items()
            ]
            phases = [
                [("load", f"load-{i}", "", query) for i, query in enumerate(loads)]
                + [("write", "checkpoint", "", "CHECKPOINT")]
                + checks,
                checks
                + [
                    ("export", "full-body", "", ingestion.export_query("duckdb")),
                    (
                        "export",
                        "native-compression",
                        "",
                        "COPY (SELECT * FROM pragma_storage_info('trades')) TO '/verification/storage-info.csv' (HEADER TRUE)",
                    ),
                ],
            ]
            if engine == "rayforce":
                phases = [
                    [("load", f"load-{i}", "", query) for i, query in enumerate(loads)]
                    + [
                        (
                            "write",
                            "save-trades",
                            "",
                            '(.db.splayed.set "/verification/trades" trades {compress: true durable: true})',
                        ),
                        (
                            "write",
                            "save-customers",
                            "",
                            '(.db.splayed.set "/verification/customers" customers {compress: true durable: true})',
                        ),
                    ]
                    + checks,
                    [
                        (
                            "load",
                            "reopen-trades",
                            "",
                            '(set trades (.db.splayed.get "/verification/trades"))',
                        ),
                        (
                            "load",
                            "reopen-customers",
                            "",
                            '(set customers (.db.splayed.get "/verification/customers"))',
                        ),
                    ]
                    + checks
                    + [("export", "full-body", "", ingestion.export_query(engine))],
                ]
            expected_export = ingestion.oracle(fixture["rows"], 0)
            dim_export = (
                "COPY (SELECT customer,region,factor FROM customers ORDER BY customer) TO '/verification/customers.csv' (HEADER TRUE)"
                if engine == "duckdb"
                else '(.csv.write (select {from: customers asc: customer}) "/verification/customers.csv")'
            )
            phases[1].append(("export", "dimension-body", "", dim_export))
        ingestion.save(
            root / "intent.json",
            {
                "engine": engine,
                "fixture": fixture,
                "expected_export": expected_export,
                "scope": "Immutable input/snapshot intent, not a per-mutation live ACK witness. Live failure testing still requires its own writer/reader journal.",
            },
        )
        records = []
        for stage, operations in enumerate(phases, 1):
            plan.write_text("".join("\t".join(op) + "\n" for op in operations))
            records.append(
                bench.run_case(
                    "cpp",
                    engine,
                    stage,
                    root,
                    0,
                    1,
                    record_count=len(operations)
                    + int(
                        any(
                            op[0] in {"write", "append", "columnar"}
                            for op in operations
                        )
                    ),
                    verification_path=storage,
                    database_file="/verification/state.duckdb"
                    if engine == "duckdb"
                    else None,
                    sample_storage=True,
                )
            )
        verification = ingestion.verify_export(
            storage / ("trades.csv" if full_snapshot else "body.csv"),
            expected_export,
        )
        dimension_verification = None
        if full_snapshot:
            dim = bench.RESULTS / "data/customers.csv"
            dimension_verification = ingestion.verify_export(
                storage / "customers.csv",
                {
                    "rows": 10000,
                    "bytes": dim.stat().st_size,
                    "sha256": fixture["sha256"]["customers.csv"],
                },
            )
        headers = (
            encoded_headers(storage, fixture["rows"])
            if engine == "rayforce" and full_snapshot
            else []
        )
        if engine == "rayforce":
            for stage in (1, 2):
                inspection = json.loads(
                    (
                        root
                        / f"rayforce-duckdb-{root.name[:8]}-cpp-rayforce-{stage}-inspect.json"
                    ).read_text()
                )
                if "RAYFORCE_NO_FSYNC=0" not in inspection["Config"]["Env"]:
                    raise RuntimeError(
                        "Rayforce persistence requires effective RAYFORCE_NO_FSYNC=0"
                    )
        files = [
            {
                "name": str(p.relative_to(storage)),
                "bytes": p.stat().st_size,
                "allocated_bytes": p.stat().st_blocks * 512,
            }
            for p in sorted(storage.rglob("*"))
            if p.is_file()
        ]
        result = {
            "passed": True,
            "scope": f"{engine} persisted snapshot with complete body and grouped/join checks after reopen in two independent C++ processes; not a crash-recovery test or repeated performance benchmark. Live journal/fault cases remain unrun."
            if full_snapshot
            else "Two-row DuckDB persistent-file API capability only; million-row and crash/journal cases remain unrun.",
            "fixture": fixture,
            "environment": environment,
            "contract": json.loads(
                (bench.ROOT / "persistence-contract.json").read_text()
            ),
            "records": records,
            "verification": verification,
            "dimension_verification": dimension_verification,
            "storage_files": files,
            "native_column_headers": headers,
            "native_store_bytes": sum(
                f["bytes"]
                for f in files
                if (
                    f["name"].startswith(("trades/", "customers/"))
                    if engine == "rayforce"
                    else f["name"].startswith("state.duckdb")
                )
            ),
            "native_storage_info_csv": (storage / "storage-info.csv").read_text()
            if (storage / "storage-info.csv").exists()
            else None,
            "native_compression_scope": "DuckDB storage_info reports codecs; Rayforce explicitly requests compressed durable column saves, with native files retained. File footprints include metadata; no pure codec ratio is inferred from a whole-database size. CSV gzip is a separate measured layer.",
            "source_sha256": {
                str(p.relative_to(bench.ROOT)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in [
                    Path(__file__),
                    bench.ROOT / "experiment.py",
                    *sorted(
                        (bench.ROOT / "analytics-workload/native").glob("bridge.*")
                    ),
                    bench.ROOT / "analytics-workload/cpp/main.cpp",
                    bench.ROOT / "docker-compose.yml",
                    bench.ROOT / "persistence-contract.json",
                ]
            },
            "host_controller": {
                "wall_ns": time.perf_counter_ns() - started,
                "cpu_seconds": resource.getrusage(resource.RUSAGE_SELF).ru_utime
                + resource.getrusage(resource.RUSAGE_SELF).ru_stime
                - before.ru_utime
                - before.ru_stime,
                "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                * (1 if os.uname().sysname == "Darwin" else 1024),
                "child_cli_cpu_seconds": resource.getrusage(
                    resource.RUSAGE_CHILDREN
                ).ru_utime
                + resource.getrusage(resource.RUSAGE_CHILDREN).ru_stime
                - children_before.ru_utime
                - children_before.ru_stime,
                "scope": "Host controller and Docker CLI children. Export gzip/hash costs overlap this total; native engine/container metrics are separate.",
            },
            "disk_scope": "Owned directory began empty. Per-process samples retain native state, observable WAL/temp files and CSV exports separately. Final file sizes are settled after graceful close/reopen; sampling can miss shorter peaks and records rename races.",
        }
        ingestion.save(root / "result.json", result)
        if full_snapshot:
            (storage / "trades.csv").unlink()
            (storage / "customers.csv").unlink()
            result["verified_export_removed"] = True
            ingestion.save(root / "result.json", result)
        print(engine, "native persistent-file probe passed:", root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["capability", "duckdb-snapshot", "rayforce-snapshot"]
    )
    args = parser.parse_args()
    run_probe(
        args.action != "capability",
        "rayforce" if args.action == "rayforce-snapshot" else "duckdb",
    )
