# TimescaleDB — single node

One PostgreSQL 18 server with the [TimescaleDB](https://github.com/timescale/timescaledb)
extension, from `timescale/timescaledb-ha` (the image Timescale builds for its own cloud:
TimescaleDB, the `timescaledb_toolkit` hyperfunctions, pgvector/pgvectorscale, PostGIS, Patroni,
`timescaledb-parallel-copy`). The image's own `psql` is the client.

```bash
make up        # start, wait for pg_isready, print version() and the installed extensions
make test      # run sql/*.sql (each file is printed before its output), see below
make benchmark # timescaledb-parallel-copy ingest, then queries on rowstore / columnstore / continuous aggregate
make status    # container state, hypertables with chunk counts and sizes, background jobs
make cli       # interactive psql
make down      # remove the containers and volumes
```

- PostgreSQL protocol on `localhost:5450` (override with `TIMESCALEDB_PORT`), user `postgres`,
  fixed demo password `tsdb-demo` (`PGPASSWORD=tsdb-demo psql -h localhost -p 5450 -U postgres`).
- Image `timescale/timescaledb-ha:pg18.6-ts2.30.2` (override with `TIMESCALEDB_TAG`):
  PostgreSQL 18.6, TimescaleDB 2.30.2, toolkit 1.26.0. Multi-arch, runs natively on Apple silicon.
  It is ~650 MB to pull and 3 GB on disk. The smaller `timescale/timescaledb` (Alpine) image has
  the same TimescaleDB but no toolkit (and so no `percentile_agg`, `time_weight`, `candlestick_agg`),
  which `sql/04` uses.
- License: the image ships the Timescale License (TSL) build (`timescaledb.license = timescale`).
  Columnstore, continuous aggregates, policies and hyperfunctions are free to self-host. The TSL
  only forbids offering TimescaleDB as a hosted database service. The `-oss` tags are Apache-2.0
  and have none of these features.
- On first start the image runs `timescaledb-tune`. `TS_TUNE_MEMORY=6GB` / `TS_TUNE_NUM_CPUS=4`
  size it for the benchmark budget rather than the whole Docker VM. It sets `shared_buffers=1536MB`
  and 16 TimescaleDB background workers. Telemetry is off (`TIMESCALEDB_TELEMETRY=off`).

What `make test` runs:

| file | shows |
|---|---|
| [`sql/01-hypertable.sql`](sql/01-hypertable.sql) | `CREATE TABLE ... WITH (tsdb.hypertable, ...)` with 1-day chunks; 200 sensors x 30 days x 5 min = 1.7M rows ending `now()`; `timescaledb_information.chunks`; chunk exclusion in `EXPLAIN`; a join with a plain `sensors` table |
| [`sql/02-columnstore.sql`](sql/02-columnstore.sql) | `convert_to_columnstore` on chunks older than 3 days (segmentby `sensor_id`, orderby `time DESC`), `hypertable_columnstore_stats` before/after (104 MB -> 25 MB, 4.2x on this random data), `ColumnarScan` + `VectorAgg` in `EXPLAIN`, `INSERT`/`UPDATE`/`DELETE` on a columnstore chunk, replacing the default columnstore policy |
| [`sql/03-continuous-aggregates.sql`](sql/03-continuous-aggregates.sql) | an hourly continuous aggregate with real-time aggregation (`materialized_only = false`), a refresh policy, a daily aggregate on top of it (hierarchical), the same answer from raw rows (~200 ms) and from the aggregate (~30 ms), a fresh row showing up before any refresh |
| [`sql/04-gapfill-hyperfunctions.sql`](sql/04-gapfill-hyperfunctions.sql) | `time_bucket`, `time_bucket_gapfill` with `locf()` and `interpolate()` over a 3-hour hole, `last()`, toolkit `percentile_agg`/`approx_percentile`, `stats_agg`, `time_weight` vs plain `avg`, `candlestick_agg` |
| [`sql/05-retention-jobs.sql`](sql/05-retention-jobs.sql) | `drop_chunks` older than 14 days (31 -> 15 chunks) while the daily continuous aggregate keeps all 30 days, `add_retention_policy`, `timescaledb_information.jobs` / `job_stats` |

Notes from running it:

- `CREATE TABLE ... WITH (tsdb.hypertable)` turns the columnstore on and adds a columnstore
  policy (`compress_after` = chunk interval, here 1 day) by itself. `sql/02` replaces it with a
  3-day policy.
- `convert_to_columnstore`, `add_columnstore_policy` and `remove_columnstore_policy` are
  procedures (`CALL`), so `sql/02` loops over `show_chunks()` in a `DO` block. `locf()` and
  `interpolate()` must be the outermost call, so the rounding goes inside them.
- `make test` is rerunnable: `sql/01` drops and recreates everything.

## Benchmark

`make benchmark` ([`bench/run.sh`](bench/run.sh), in the same image) generates `DEVICES` x
`DAYS` x 1440 readings (one per device per minute: a daily temperature curve plus noise with
2 decimals, and humidity with 1 decimal), 10.08M rows by default, as a 364 MiB CSV. Then:

1. **Ingest.** [`timescaledb-parallel-copy`](https://github.com/timescale/timescaledb-parallel-copy)
   loads the CSV into a fresh hypertable (1-day chunks, plus an index on `(device_id, time DESC)`)
   with 1, 4 and 8 workers (`WORKERS`), in `COPY` batches of `BATCH` (5,000) rows, in two modes.
   By default it writes **straight into the columnstore** ("direct compress": each batch is
   compressed during the `COPY`). With `--disable-direct-compress` it writes into the
   **rowstore**, the plain Postgres heap plus its indexes.
2. **Queries on the rowstore.** The last rowstore table runs 5 dashboard queries
   ([`bench/queries.sql`](bench/queries.sql)) `RUNS` (5) times each: hourly average over all
   devices, daily max per device, 1 day of one device in 5-minute buckets, the latest reading per
   device (`DISTINCT ON`), and a count over a threshold.
3. **Columnstore.** `convert_to_columnstore` on every chunk (timed), the size before and after,
   then the same queries again.
4. **Continuous aggregate.** An hourly aggregate per device is built (timed), and the first two
   queries are answered from it ([`bench/cagg-queries.sql`](bench/cagg-queries.sql)).

```bash
make benchmark                        # 1000 devices x 7 days = 10.08M rows, 1/4/8 workers
make benchmark SMOKE=1                # 1 day (1.44M rows), 4 workers, 3 runs per query
make benchmark DEVICES=5000 DAYS=2 WORKERS="8 16" BATCH=10000
```

It prints a summary and keeps the raw output plus a parsed JSON (versions, parameters, Docker VM,
limits) in `results/timescaledb-single-<UTC time>.{txt,json}` (gitignored). The parser is
[`bench/report.py`](bench/report.py), standard-library Python run with `uv run --frozen` in
`ghcr.io/astral-sh/uv`. Client and server share one Docker VM.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) caps the `timescaledb`
container at `BENCH_CPUS=4` / `BENCH_MEM=6g` (no swap) with `docker update`, and restores the old
limits afterwards. Docker cannot remove a memory limit from a running container, so
"unlimited" comes back as the Docker VM's total memory; `make down && make up` starts clean.
The bench client has `cpus: 2` (`BENCH_CLIENT_CPUS`). The JSON records the applied limits under
`limits`.

### Sample results

2026-10-02, `make benchmark` (defaults), Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM:
11 CPUs, 24.4 GB, aarch64, native arm64 image), PostgreSQL 18.6 + TimescaleDB 2.30.2,
timescaledb-parallel-copy v0.11.0, server capped at 4 CPUs / 6 GB, client 2 CPUs.

| ingest into | workers | rows/s | seconds | size on disk |
|---|---:|---:|---:|---:|
| columnstore (direct compress) | 1 | 868,838 | 11.6 | 150 MiB |
| columnstore (direct compress) | 4 | 2,256,543 | 4.5 | 151 MiB |
| columnstore (direct compress) | 8 | 2,975,795 | 3.4 | 151 MiB |
| rowstore | 1 | 212,523 | 47.4 | 1,154 MiB |
| rowstore | 4 | 278,894 | 36.1 | 1,159 MiB |
| rowstore | 8 | 524,009 | 19.2 | 1,165 MiB |

Storage: 7 chunks. The rowstore takes 1,164 MiB (580 MiB heap + 584 MiB for the two indexes).
After `convert_to_columnstore` (5.5 s for all 10M rows) the table is 151 MiB, **7.7x smaller**,
the same size as the direct-compress ingest. The hourly continuous aggregate (168,000 rows) took 2.1 s
to build.

| query (median of 5, ms) | rowstore | columnstore | continuous aggregate |
|---|---:|---:|---:|
| hourly-avg-all (10M rows -> 168 buckets) | 368.8 | 134.0 | 17.1 |
| daily-max-per-device (10M rows -> 7,000 groups) | 1,302.0 | 399.4 | 27.9 |
| one-device-1day (1,440 rows) | 2.6 | 5.2 | - |
| lastpoint (latest per device, last hour) | 51.6 | 15.2 | - |
| threshold-count (filter + group by device) | 178.6 | 36.4 | - |

- **Ingest:** writing straight into the columnstore is 4-6x faster than the rowstore at the same
  worker count. It writes 7.7x fewer bytes and maintains no B-tree indexes, since the columnstore keeps
  min/max metadata per batch. The rowstore ingest is bound by WAL and index maintenance.
- **Scans:** the columnstore makes the aggregates that read every row 3-5x faster (vectorized
  aggregation over only the columns used). Continuous aggregates are another 8-14x on top of that:
  they read 168k pre-aggregated rows instead of 10M.
- **Point reads:** one device's last day is the one query where the rowstore wins (2.6 vs 5.2 ms):
  the B-tree finds its 1,440 rows directly, while the columnstore decompresses a batch of up to
  1,000 rows per segment. This is why recent chunks stay in the rowstore and a policy converts
  them as they age.
