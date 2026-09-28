# DuckDB

Website: https://duckdb.org/

- [`single-node/`](single-node) — the DuckDB CLI in Docker over ~1M rows of seeded random CSV/NDJSON: auto-detected reads, joins, QUALIFY/window functions, UNNEST, PIVOT and hive-partitioned Parquet.

## Benchmark

TPC-H SF1 on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-09-28): all 22 queries in 0.43 s
(geometric mean 15 ms, slowest 55 ms); the same queries on Parquet files run ~3.3× slower than on
DuckDB's native storage. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).
