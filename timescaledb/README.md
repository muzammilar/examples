# TimescaleDB

Website: https://www.tigerdata.com/timescaledb

TimescaleDB is a PostgreSQL extension for time series: hypertables partitioned into time
chunks, a columnstore for older chunks, continuous aggregates, and retention and
columnstore policies. It is plain SQL on Postgres, so joins with relational tables work.

- [`single-node/`](single-node) — one PostgreSQL 18 + TimescaleDB 2.30 server (`timescale/timescaledb-ha`) on Docker Compose, with a SQL walkthrough: hypertables and chunks, columnstore compression, continuous aggregates with real-time aggregation, `time_bucket_gapfill`, toolkit hyperfunctions, and retention.
- [`fleet-telemetry-showcase/`](fleet-telemetry-showcase) — a Rust client (`tokio-postgres`, binary `COPY`) that simulates 1000 electric delivery vehicles (14.4M readings). It times the ingest into the rowstore and straight into the columnstore, the compression ratio, and fleet dashboard queries joined with relational metadata, on the rowstore, the columnstore and a continuous aggregate.

There is no sharded (distributed) cluster example. TimescaleDB's multi-node mode (distributed
hypertables) was deprecated in 2.13 and removed in 2.14
([CHANGELOG](https://github.com/timescale/timescaledb/blob/main/CHANGELOG.md),
[MultiNodeDeprecation.md](https://github.com/timescale/timescaledb/blob/main/docs/MultiNodeDeprecation.md)).
A self-hosted TimescaleDB cluster is now Postgres streaming replication: one primary and
replicas that each hold all the data, for HA and read scaling, not sharding.

## Benchmark

`timescaledb-parallel-copy` ingest of 10.08M sensor readings, then dashboard queries, on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-10-02). Writing straight into the columnstore reaches 2.98M rows/s with 8 workers, against 524k rows/s into the rowstore. The columnstore is 7.7x smaller (151 MiB vs 1,164 MiB), and full-scan aggregates run 3-5x faster on it (daily max per device: 1,302 -> 399 ms). A continuous aggregate answers the same query in 28 ms. Full tables and method: [`single-node/README.md`](single-node/README.md#benchmark).

Fleet telemetry showcase (Rust client, 4 binary `COPY` connections, server capped at 4 CPUs / 6 GB, same hardware, 2026-10-03): 14.4M readings from 1000 vehicles load at 5.8M rows/s straight into the columnstore, against 1.1M rows/s into the rowstore. The columnstore takes 213 MiB against 2,020 MiB (9.5x smaller). Dashboard tiles that join telemetry with `vehicles`/`fleets` take 27-41 ms on an hourly continuous aggregate, 0.63-0.66 s on the columnstore and 0.76-1.3 s on the rowstore. Details: [`fleet-telemetry-showcase/README.md`](fleet-telemetry-showcase/README.md#results).

## Known issues

TimescaleDB is actively maintained: 2.30.2 shipped on 2026-09-29, and minor releases come
roughly monthly. The company behind it renamed itself Tiger Data in 2025, so docs and links
move between timescale.com and tigerdata.com. The API is still changing. The "compression"
functions became the "columnstore" functions in 2.18, and older blog posts and answers use
names and call styles that no longer work. Seen while building these examples (2026-10-02,
TimescaleDB 2.30.2, `timescale/timescaledb-ha:pg18.6-ts2.30.2`):

- The columnstore functions are procedures. `SELECT convert_to_columnstore(c) FROM show_chunks(...)`
  fails with `ERROR: convert_to_columnstore(regclass, if_not_columnstore => boolean) is a procedure`
  (`HINT: To call a procedure, use CALL.`). The same goes for `add_columnstore_policy` and
  `remove_columnstore_policy`. The examples `CALL` them, inside a `DO` loop over `show_chunks()`
  where there are several chunks.
- `CREATE TABLE ... WITH (tsdb.hypertable)` adds a columnstore policy on its own, with
  `compress_after` set to the chunk interval. A later `add_columnstore_policy(..., if_not_exists => true)`
  only prints `WARNING: columnstore policy already exists for hypertable "conditions"` and keeps
  the old one, so the walkthrough removes it and adds its own.
- `timescaledb-parallel-copy` v0.11 writes straight into the columnstore by default (direct
  compress). For chunks written that way, `hypertable_columnstore_stats` returns NULL
  before/after sizes, so there is no ratio to read. The benchmark uses `--disable-direct-compress`
  to get a rowstore table, measures it, and then converts it.
- `locf(round(avg(x), 2))` works, but `round(locf(avg(x)), 2)` fails with
  `ERROR: locf must be toplevel function call`. The same applies to `interpolate()`.
- Multi-node (distributed hypertables) was deprecated in 2.13 and removed in 2.14 (January 2024).
  Scaling out now means Postgres streaming replication for HA and read replicas, not sharding.
- `timescale/timescaledb-ha` is ~650 MB to pull and 3 GB unpacked. The Alpine
  `timescale/timescaledb` image (~500 MB) has no `timescaledb_toolkit`. On a shared, nearly full
  Docker VM, the 10M-row benchmark once failed with
  `ERROR: could not extend file "base/5/84390": No space left on device`. Keep ~5 GB free.
