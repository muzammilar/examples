"""Exactly one DuckDB owner process; clients access it through Quack."""

import time
import duckdb

connection = duckdb.connect("/srv/ducklab/data/events.duckdb")
connection.execute("SET enable_progress_bar=false")
connection.execute("SET threads=2")
connection.execute("SET checkpoint_threshold='256MB'")
connection.execute("SET memory_limit='512MB'")
connection.execute("SET extension_directory='/opt/duckdb_extensions'")
connection.execute("LOAD quack")
connection.execute(
    "CALL quack_serve('quack:0.0.0.0:9494', token := 'local-lab-token', allow_other_hostname := true)"
)
while True:
    time.sleep(60)
