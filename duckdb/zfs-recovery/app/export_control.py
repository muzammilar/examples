"""Verify native COPY bytes against the established standard-library CSV dialect."""

import csv
import hashlib
import json
from pathlib import Path
import tempfile

import duckdb
from export_csv import copy_csv

results = []
with tempfile.TemporaryDirectory(prefix="ducklab-copy-control-") as directory:
    root = Path(directory)
    with duckdb.connect() as connection:
        connection.execute("SET memory_limit='64MB'")
        for name, payload in (
            ("legacy", ""),
            ("live", ",sha256(CAST(i AS VARCHAR)) payload"),
        ):
            connection.execute(
                "CREATE OR REPLACE TABLE events AS SELECT i event_id,"
                "CAST(i%10000 AS INTEGER) customer_id,i%10000+1 amount_cents"
                + payload
                + ",0::BIGINT batch_start,10::BIGINT batch_end FROM range(10) t(i)"
            )
            value = connection.execute("SELECT * FROM events ORDER BY event_id")
            expected = root / (name + "-expected.csv")
            with expected.open("x", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow([field[0] for field in value.description])
                writer.writerows(value.fetchall())
            actual = root / (name + "-native.csv")
            copy_csv(connection, "events", actual)
            if actual.read_bytes() != expected.read_bytes():
                raise RuntimeError("native CSV dialect differs: " + name)
            try:
                copy_csv(connection, "events", actual)
            except FileExistsError:
                pass
            else:
                raise RuntimeError("native export overwrote an earlier file")
            results.append(
                {
                    "shape": name,
                    "rows": 10,
                    "bytes": actual.stat().st_size,
                    "sha256": hashlib.sha256(actual.read_bytes()).hexdigest(),
                }
            )
print(
    json.dumps(
        {
            "passed": True,
            "cases": results,
            "scope": "In-memory byte-equivalence/overwrite controls, not native recovery or performance qualification.",
        }
    )
)
