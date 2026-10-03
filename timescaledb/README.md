# TimescaleDB

Website: https://www.tigerdata.com/timescaledb

TimescaleDB is a PostgreSQL extension for time series: hypertables partitioned into time
chunks, a columnstore for older chunks, continuous aggregates, and retention and
columnstore policies. It is plain SQL on Postgres, so joins with relational tables work.

- [`single-node/`](single-node) — one PostgreSQL 18 + TimescaleDB 2.30 server (`timescale/timescaledb-ha`) on Docker Compose, with a SQL walkthrough: hypertables and chunks, columnstore compression, continuous aggregates with real-time aggregation, `time_bucket_gapfill`, toolkit hyperfunctions, and retention.

## Benchmark

`timescaledb-parallel-copy` ingest of 10.08M sensor readings, then dashboard queries, on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-10-02). Writing straight into the columnstore reaches 2.98M rows/s with 8 workers, against 524k rows/s into the rowstore. The columnstore is 7.7x smaller (151 MiB vs 1,164 MiB), and full-scan aggregates run 3-5x faster on it (daily max per device: 1,302 -> 399 ms). A continuous aggregate answers the same query in 28 ms. Full tables and method: [`single-node/README.md`](single-node/README.md#benchmark).
