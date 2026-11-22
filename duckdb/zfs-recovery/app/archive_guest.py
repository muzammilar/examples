"""Recover an isolated ZFS clone, then export it with DuckDB's read-only API."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import resource
import time

import duckdb
import export_csv

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=("recover", "export"))
parser.add_argument("database", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("tables", nargs="*")
args = parser.parse_args()
if not re.fullmatch(
    r"/srv/ducklab/verify-[0-9a-f]{32}/events[.]duckdb", str(args.database)
):
    raise ValueError("only an independently named verification clone may be opened")
started = time.monotonic_ns()
with duckdb.connect(str(args.database), read_only=args.mode == "export") as connection:
    connection.execute("SET enable_progress_bar=false")
    connection.execute("SET threads=2")
    connection.execute("SET memory_limit='512MB'")
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE' ORDER BY table_name"
        ).fetchall()
    ]
    if args.mode == "recover":
        connection.execute("CHECKPOINT")
    else:
        args.output.mkdir()
        for table in args.tables:
            if not re.fullmatch(r"live_[0-9a-f]{32}|events", table):
                raise ValueError("unexpected example-owned table")
            export_csv.copy_csv(connection, table, args.output / (table + ".csv"))

value = resource.getrusage(resource.RUSAGE_SELF)
print(
    json.dumps(
        {
            "mode": args.mode,
            "duckdb_version": duckdb.__version__,
            "source_sha256": {
                name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in {
                    "archive_guest.py": Path(__file__),
                    "export_csv.py": Path(export_csv.__file__),
                }.items()
            },
            "tables": tables,
            "wall_ns": time.monotonic_ns() - started,
            "user_cpu_seconds": value.ru_utime,
            "system_cpu_seconds": value.ru_stime,
            "peak_rss_bytes": value.ru_maxrss * 1024,
            "native_process_io": Path("/proc/self/io").read_text(),
            "scope": "Guest-local native DuckDB process; kernel IO accounting is inside QEMU, not host physical IO. Writable recovery only touches a fresh clone; export uses a read-only connection.",
        }
    )
)
