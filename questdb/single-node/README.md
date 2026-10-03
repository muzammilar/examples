# QuestDB — single node

One QuestDB server from the official image `questdb/questdb:10.0.1` (override with `QUESTDB_VERSION`), data in a named volume, telemetry off. SQL walkthrough plus an ILP ingest / query benchmark.

## Quick start

```bash
make up         # start and wait for the health check
make test       # sql/*.sql through psql, then ILP over HTTP into a dedup table
make benchmark  # ILP ingest rate + query timings (bench/bench.py); SMOKE=1 for a short run
make status     # container, version, tables and WAL state
make cli        # interactive psql (postgres:17-alpine) over the PG wire protocol
make down       # remove the containers and the data volume
```

| port | env | what |
|------|-----|------|
| 9000 | `QUESTDB_HTTP_PORT` | web console (http://localhost:9000), REST (`/exec`, `/imp`, `/exp`), ILP over HTTP (`/write`) |
| 8812 | `QUESTDB_PG_PORT` | PostgreSQL wire protocol, user `admin`, password `quest`, database `qdb` |
| 9009 | `QUESTDB_ILP_PORT` | ILP over TCP |
| 9003 | `QUESTDB_HEALTH_PORT` | health check (`/`) and Prometheus metrics (`/metrics`) |

## What `make test` runs

Each file in [`sql/`](sql) is printed with its results. Data is random on every run (`rnd_*` is unseeded), so numbers differ but not the shape.

| step | shows |
|---|---|
| [`01-schema.sql`](sql/01-schema.sql) | `trades` and `quotes` with a designated timestamp (`TIMESTAMP(ts)`: rows stored in time order), `PARTITION BY DAY` (one directory per day) and `WAL` (writes go to a write-ahead log applied in the background, so several connections can write one table, out-of-order rows get merged in, and dedup works). `SYMBOL` columns are interned strings stored as ints. |
| [`02-data.sql`](sql/02-data.sql) | 2M quotes and 1M trades over three days, generated in SQL (`long_sequence`, `timestamp_sequence`, `rnd_*`): sine-wave mid price, 1 bp quotes, buys ~1 bp above the mid, sells ~1 bp below. INSERTs return once rows are in the WAL; `make test` then waits until `wal_tables()` shows every writer caught up. |
| [`03-sample-by.sql`](sql/03-sample-by.sql) | `SAMPLE BY 1h` OHLCV candles for one day (`ts IN '2026-09-30'` opens only that partition), 250 ms buckets with `FILL(PREV)`, daily VWAP per symbol, `EXPLAIN` showing an `Interval forward scan` over one day |
| [`04-latest-on.sql`](sql/04-latest-on.sql) | `LATEST ON ts PARTITION BY symbol`: top of book per symbol, the same as of a point in time (filter first, then latest), last trade per (symbol, side) |
| [`05-asof-join.sql`](sql/05-asof-join.sql) | `trades ASOF JOIN quotes ON (symbol)`: every trade with the quote in force at its timestamp, execution quality in bp vs. the mid (buys +1.0 bp, sells -1.0 bp, as generated), `TOLERANCE 300T` dropping quotes older than 300 ms |
| [`06-dedup.sql`](sql/06-dedup.sql) | `candles` with `DEDUP UPSERT KEYS(ts, symbol)`: a second INSERT with the same (ts, symbol) replaces the 10:00 BTC row instead of adding one; an older (out-of-order) row lands in its place in time order |
| ILP over HTTP | POSTs the same two ILP lines to `/write` twice (curl inside the container). The BTC 10:00 candle is replaced again and the new SOL 12:00 candle exists once: re-sending a batch is idempotent on a dedup table. `candles` ends with 5 rows. |

## Benchmark

[`bench/bench.py`](bench/bench.py) runs in the `bench` service (`ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`, `uv run --frozen`, pinned in [`bench/uv.lock`](bench/uv.lock)) with the official Python client (`questdb==5.0.0`, a wrapper around the C client) and pandas:

1. Drops and recreates `bench_trades` and `bench_quotes` (WAL, `PARTITION BY DAY`).
2. **Ingest.** `PROCS` (8) processes, each with its own ILP-over-HTTP sender, send `ROWS` rows (20M: 1/3 trades, 2/3 quotes, 100 symbols, 3 days) as DataFrames of 100k rows, one HTTP request each. Each process owns a slice of the symbols and walks the same 3 days, so commits overlap in time and the WAL apply merges them out of order. Reports `sent` (every request acked: rows durable in the WAL) and `visible` (WAL applied, `count()` sees all rows).
3. **Queries** over HTTP `/exec`, 5 runs each after one warm-up: client wall time (min / p50 / max) and QuestDB's own `execute` time.
4. Writes `results/questdb-single-<UTC time>.json` (gitignored) and drops the tables (`KEEP=1` keeps them; 20M rows take ~925 MiB).

```bash
make benchmark                        # 20M rows from 8 senders
make benchmark SMOKE=1                # 3M rows
make benchmark ROWS=50000000 PROCS=6 KEEP=1
```

Limits: [`bench/limits.sh`](bench/limits.sh) caps the `questdb` container at `BENCH_CPUS=4` / `BENCH_MEM=8g` (no swap) with `docker update` and restores the old limits afterwards (an unlimited memory limit comes back as the VM's total; `make down && make up` starts clean). Bench client: `cpus: 6` (`BENCH_CLIENT_CPUS`). QuestDB sizes its worker pools from the CPUs it sees at start (11 here), so the cap limits CPU time, not thread count.

### Sample results

2026-10-02, four `make benchmark` runs (defaults), Docker Desktop 29.5.3, Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64, native image), QuestDB 10.0.1, server 4 CPUs / 8 GB, client 6 CPUs. The Docker VM was shared with other running containers, hence the wide range.

| ingest, 20M rows | runs |
|---|---|
| sent (acked into the WAL) | 2.18M, 2.70M, 3.14M, 3.02M rows/s |
| visible (WAL applied) | 1.87M, 2.52M, 2.14M, 2.80M rows/s (7.2–10.7 s for 20M rows) |

| query (p50 ms over 5 runs, four runs) | result rows | wall p50 ms | server p50 ms |
|---|--:|---|---|
| `count()` of 6.7M trades | 1 | 0.7–3.7 | 0.14–0.73 |
| `count(), avg(price)` over one hour (`ts IN '…T12;1h'`) | 1 | 0.9–1.4 | 0.27–0.51 |
| 1-minute OHLCV for one symbol, 3 days (`SAMPLE BY 1m`) | 4,320 | 17–29 | 9–25 |
| hourly VWAP for 100 symbols, 3 days (`SAMPLE BY 1h`, full scan) | 7,200 | 74–169 | 70–162 |
| `LATEST ON ts PARTITION BY symbol` on 13.3M quotes | 100 | 1.1–4.2 | 0.3–0.8 |
| `ASOF JOIN` trades→quotes, one hour (~93k trades) | 1 | 61–172 | 60–170 |
| `ASOF JOIN` trades→quotes, one day (2.2M trades), per symbol | 100 | 1,345–2,991 | 1,344–2,987 |

- Ingest: 8 ILP/HTTP senders land 2–3M rows/s on 4 CPUs; the WAL apply keeps up (rows visible 0.5–3 s after the last ack) even with the senders' rows interleaved in time.
- Time-filtered and `LATEST ON` queries stay around 1 ms: they touch one partition or read from the end of the table.
- `SAMPLE BY` over all 6.7M trades runs in parallel (`Async Group By`).
- `ASOF JOIN` (`AsOf Join Fast`) is a single-threaded merge with no time filter on the quotes side, so a one-day join over 2.2M trades costs seconds and varies most with VM load.

## Known issues

QuestDB 10.0.1, `questdb==5.0.0`, 2026-10-02.

- **Empty `QDB_*` variable stops the server** at startup (exit 55): `io.questdb.ServerConfigurationException: invalid configuration value [key=shared.worker.count, value=]` (from `QDB_SHARED_WORKER_COUNT=`). Workaround: leave the key out instead of setting it empty.
- **Single-letter duration units** in `SAMPLE BY` and `TOLERANCE`: `SAMPLE BY 100ms` fails with `expected single letter qualifier`. Milliseconds are `T` (`SAMPLE BY 250T`, `TOLERANCE 300T`).
- **`round(x, 2)`** does not always return the nearest double, so psql shows values like `64765.270000000004`. Workaround: the walkthrough generates prices as whole cents `/ 100.0` and casts computed results to `decimal(18,2)`.
- **Full Docker VM disk.** `CREATE TABLE` over `/exec` fails with only `Could not create table, could not create [dir=/var/lib/questdb/db/bench_trades~14]`; the server log has the cause (`CairoException: [28]`, ENOSPC). The benchmark drops its tables at the end (~925 MiB for 20M rows) for this reason.
- **`ASOF JOIN` is single-threaded** (`AsOf Join Fast` in `EXPLAIN`): one day of 2.2M trades takes 1.3–3.0 s while the parallel `SAMPLE BY` over all 6.7M trades takes under 0.2 s. Narrowing the quotes side with a subquery (`ASOF JOIN (SELECT * FROM quotes WHERE ts > …)`) was slower (626 ms vs. 299 ms in one test), so the queries join the table directly.
- **Plan cache log noise.** After a table is dropped and recreated, statements cached for open PG connections log `E ... cached query plan cannot be used because table schema has changed`. QuestDB recompiles them; the client sees no error.
- **Python client deprecation.** `questdb` 5.0.0 warns `DeprecationWarning: questdb.ingress is deprecated; import from questdb instead (or use questdb.connect() for QWP/WebSocket)`. The benchmark imports `from questdb import Sender`.
