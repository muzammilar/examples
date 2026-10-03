# TimescaleDB — single node

One PostgreSQL 18 server with the [TimescaleDB](https://github.com/timescale/timescaledb) extension, from `timescale/timescaledb-ha`, plus a SQL walkthrough and an ingest/query benchmark. The image's own `psql` is the client.

## Quick start

```bash
make up        # start, wait for pg_isready, print version() and the installed extensions
make test      # run sql/*.sql (each file is printed before its output)
make benchmark # timescaledb-parallel-copy ingest, then queries on rowstore / columnstore / continuous aggregate
make status    # container state, hypertables with chunk counts and sizes, background jobs
make cli       # interactive psql
make down      # remove the containers and volumes
```

## Setup

| item | value |
|---|---|
| port | `localhost:5450` (override `TIMESCALEDB_PORT`) |
| login | user `postgres`, password `tsdb-demo` (`PGPASSWORD=tsdb-demo psql -h localhost -p 5450 -U postgres`) |
| image | `timescale/timescaledb-ha:pg18.6-ts2.30.2` (override `TIMESCALEDB_TAG`): PostgreSQL 18.6, TimescaleDB 2.30.2, toolkit 1.26.0. Multi-arch, native on Apple silicon. ~650 MB pull, 3 GB on disk. |
| image contents | the image Timescale builds for its own cloud: TimescaleDB, `timescaledb_toolkit` hyperfunctions, pgvector/pgvectorscale, PostGIS, Patroni, `timescaledb-parallel-copy` |
| tuning | `timescaledb-tune` on first start, sized by `TS_TUNE_MEMORY=6GB` / `TS_TUNE_NUM_CPUS=4` (the benchmark budget, not the whole Docker VM): `shared_buffers=1536MB`, 16 TimescaleDB background workers |
| telemetry | off (`TIMESCALEDB_TELEMETRY=off`) |

- Why `-ha`: the smaller `timescale/timescaledb` (Alpine) image has the same TimescaleDB but no toolkit, so no `percentile_agg`, `time_weight`, `candlestick_agg`, which `sql/04` uses.
- License: the image ships the Timescale License (TSL) build (`timescaledb.license = timescale`). Columnstore, continuous aggregates, policies and hyperfunctions are free to self-host; the TSL only forbids offering TimescaleDB as a hosted database service. The `-oss` tags are Apache-2.0 and have none of these features.

## What `make test` runs

Rerunnable: `sql/01` drops and recreates everything.

| file | shows |
|---|---|
| [`sql/01-hypertable.sql`](sql/01-hypertable.sql) | `CREATE TABLE ... WITH (tsdb.hypertable, ...)` with 1-day chunks; 200 sensors x 30 days x 5 min = 1.7M rows ending `now()`; `timescaledb_information.chunks`; chunk exclusion in `EXPLAIN`; a join with a plain `sensors` table |
| [`sql/02-columnstore.sql`](sql/02-columnstore.sql) | `convert_to_columnstore` on chunks older than 3 days (segmentby `sensor_id`, orderby `time DESC`), `hypertable_columnstore_stats` before/after (104 MB -> 25 MB, 4.2x on this random data), `ColumnarScan` + `VectorAgg` in `EXPLAIN`, `INSERT`/`UPDATE`/`DELETE` on a columnstore chunk, replacing the default columnstore policy with a 3-day one |
| [`sql/03-continuous-aggregates.sql`](sql/03-continuous-aggregates.sql) | an hourly continuous aggregate with real-time aggregation (`materialized_only = false`), a refresh policy, a daily aggregate on top of it (hierarchical), the same answer from raw rows (~200 ms) and from the aggregate (~30 ms), a fresh row showing up before any refresh |
| [`sql/04-gapfill-hyperfunctions.sql`](sql/04-gapfill-hyperfunctions.sql) | `time_bucket`, `time_bucket_gapfill` with `locf()` and `interpolate()` over a 3-hour hole, `last()`, toolkit `percentile_agg`/`approx_percentile`, `stats_agg`, `time_weight` vs plain `avg`, `candlestick_agg` |
| [`sql/05-retention-jobs.sql`](sql/05-retention-jobs.sql) | `drop_chunks` older than 14 days (31 -> 15 chunks) while the daily continuous aggregate keeps all 30 days, `add_retention_policy`, `timescaledb_information.jobs` / `job_stats` |

## Benchmark

`make benchmark` ([`bench/run.sh`](bench/run.sh), same image) generates `DEVICES` x `DAYS` x 1440 readings (one per device per minute: daily temperature curve plus noise, 2 decimals; humidity, 1 decimal). Default: 10.08M rows, a 364 MiB CSV.

| step | what |
|---|---|
| 1. ingest | [`timescaledb-parallel-copy`](https://github.com/timescale/timescaledb-parallel-copy) loads the CSV into a fresh hypertable (1-day chunks, index on `(device_id, time DESC)`) with 1, 4 and 8 workers (`WORKERS`), `COPY` batches of `BATCH` (5,000) rows, in two modes: straight into the **columnstore** (default, "direct compress": each batch compressed during the `COPY`) and into the **rowstore** (`--disable-direct-compress`: plain Postgres heap plus indexes) |
| 2. rowstore queries | the last rowstore table runs 5 queries ([`bench/queries.sql`](bench/queries.sql)) `RUNS` (5) times each: hourly average over all devices, daily max per device, 1 day of one device in 5-minute buckets, latest reading per device (`DISTINCT ON`), count over a threshold |
| 3. columnstore | `convert_to_columnstore` on every chunk (timed), size before and after, same queries again |
| 4. continuous aggregate | hourly aggregate per device built (timed); first two queries answered from it ([`bench/cagg-queries.sql`](bench/cagg-queries.sql)) |

```bash
make benchmark                        # 1000 devices x 7 days = 10.08M rows, 1/4/8 workers
make benchmark SMOKE=1                # 1 day (1.44M rows), 4 workers, 3 runs per query
make benchmark DEVICES=5000 DAYS=2 WORKERS="8 16" BATCH=10000
```

- Output: summary on stdout; raw output plus parsed JSON (versions, parameters, Docker VM, limits) in `results/timescaledb-single-<UTC time>.{txt,json}` (gitignored). Parser: [`bench/report.py`](bench/report.py), standard-library Python run with `uv run --frozen` in `ghcr.io/astral-sh/uv`.
- Limits: [`bench/limits.sh`](bench/limits.sh) caps the `timescaledb` container at `BENCH_CPUS=4` / `BENCH_MEM=6g` (no swap) with `docker update` and restores the old limits afterwards. Docker cannot remove a memory limit from a running container, so "unlimited" comes back as the Docker VM's total memory; `make down && make up` starts clean. Bench client: `cpus: 2` (`BENCH_CLIENT_CPUS`). Client and server share one Docker VM. The JSON records the applied limits under `limits`.

### Sample results

2026-10-02, defaults, Docker Desktop 29.5.3, Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64, native arm64 image), PostgreSQL 18.6 + TimescaleDB 2.30.2, timescaledb-parallel-copy v0.11.0, server 4 CPUs / 6 GB, client 2 CPUs.

| ingest into | workers | rows/s | seconds | size on disk |
|---|---:|---:|---:|---:|
| columnstore (direct compress) | 1 | 868,838 | 11.6 | 150 MiB |
| columnstore (direct compress) | 4 | 2,256,543 | 4.5 | 151 MiB |
| columnstore (direct compress) | 8 | 2,975,795 | 3.4 | 151 MiB |
| rowstore | 1 | 212,523 | 47.4 | 1,154 MiB |
| rowstore | 4 | 278,894 | 36.1 | 1,159 MiB |
| rowstore | 8 | 524,009 | 19.2 | 1,165 MiB |

Storage: 7 chunks. Rowstore 1,164 MiB (580 MiB heap + 584 MiB for the two indexes). After `convert_to_columnstore` (5.5 s for all 10M rows): 151 MiB, **7.7x smaller**, same as the direct-compress ingest. Hourly continuous aggregate (168,000 rows): 2.1 s to build.

| query (median of 5, ms) | rowstore | columnstore | continuous aggregate |
|---|---:|---:|---:|
| hourly-avg-all (10M rows -> 168 buckets) | 368.8 | 134.0 | 17.1 |
| daily-max-per-device (10M rows -> 7,000 groups) | 1,302.0 | 399.4 | 27.9 |
| one-device-1day (1,440 rows) | 2.6 | 5.2 | - |
| lastpoint (latest per device, last hour) | 51.6 | 15.2 | - |
| threshold-count (filter + group by device) | 178.6 | 36.4 | - |

- Ingest: columnstore 4-6x faster than rowstore at the same worker count; 7.7x fewer bytes and no B-tree maintenance (min/max metadata per batch instead). Rowstore ingest is bound by WAL and index maintenance.
- Scans: full-row aggregates 3-5x faster on the columnstore (vectorized aggregation over only the columns used). Continuous aggregates add another 8-14x: 168k pre-aggregated rows instead of 10M.
- Point reads: one device's last day is the one query where the rowstore wins (2.6 vs 5.2 ms). The B-tree finds its 1,440 rows directly; the columnstore decompresses a batch of up to 1,000 rows per segment. Hence recent chunks stay in the rowstore and a policy converts them as they age.

## Known issues

TimescaleDB 2.30.2, `timescale/timescaledb-ha:pg18.6-ts2.30.2`, 2026-10-02.

- **Columnstore functions are procedures.** `SELECT convert_to_columnstore(c) FROM show_chunks(...)` fails with `ERROR: convert_to_columnstore(regclass, if_not_columnstore => boolean) is a procedure` (`HINT: To call a procedure, use CALL.`). Same for `add_columnstore_policy` and `remove_columnstore_policy`. Workaround: `CALL` them; `sql/02` loops over `show_chunks()` in a `DO` block.
- **Implicit columnstore policy.** `CREATE TABLE ... WITH (tsdb.hypertable)` turns the columnstore on and adds a policy with `compress_after` = chunk interval (here 1 day). A later `add_columnstore_policy(..., if_not_exists => true)` only prints `WARNING: columnstore policy already exists for hypertable "conditions"` and keeps the old one. Workaround: `sql/02` removes it and adds a 3-day policy.
- **No ratio after direct compress.** `timescaledb-parallel-copy` v0.11 writes straight into the columnstore by default. For chunks written that way, `hypertable_columnstore_stats` returns NULL before/after sizes. Workaround: the benchmark loads with `--disable-direct-compress`, measures, then converts.
- **`locf`/`interpolate` must be outermost.** `locf(round(avg(x), 2))` works; `round(locf(avg(x)), 2)` fails with `ERROR: locf must be toplevel function call`. Same for `interpolate()`.
- **Image size.** `timescale/timescaledb-ha` is ~650 MB to pull and 3 GB unpacked; the Alpine `timescale/timescaledb` (~500 MB) has no `timescaledb_toolkit`. On a shared, nearly full Docker VM the 10M-row benchmark once failed with `ERROR: could not extend file "base/5/84390": No space left on device`. Keep ~5 GB free.
