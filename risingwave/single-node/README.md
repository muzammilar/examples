# RisingWave — single node

RisingWave 3.1.0 in `single_node` mode (meta, compute, frontend and compactor in one process,
SQLite metadata, Hummock state store on the local filesystem) on Docker Compose, with a psql
walkthrough of tables, a source, materialized views, `EMIT ON WINDOW CLOSE` and sinks.

## Quick start

```bash
make up            # start RisingWave and the Postgres sink target, wait until healthy (~5 s)
make test          # run sql/*.sql through psql, then check the Postgres sink delivered the MV
make status        # workers and per-job parallelism
make cli           # interactive psql (user root, database dev)
make cli-postgres  # psql on the sink target database
make logs          # last 50 lines of the RisingWave log
make down          # remove containers, volume and network
```

## Setup

| Service | Image | Port | Role |
|---|---|---|---|
| `risingwave` | `risingwavelabs/risingwave:v3.1.0` (multi-arch, native arm64) | `127.0.0.1:4566` (Postgres wire), `127.0.0.1:5691` (dashboard) | `single_node --store-directory /data`; 4 CPUs, 8 GB |
| `postgres` | `postgres:17-alpine` | internal only | target of the native Postgres sink (database `sinkdb`) |
| `psql` (profile `tools`) | `postgres:17-alpine` | — | client for `make test` / `make cli`; the RisingWave image has no psql |

- Connect from the host: `psql -h 127.0.0.1 -p 4566 -U root -d dev` (no password).
- `RW_VERSION`, `RW_MEM`, `RW_CPUS`, `RW_PORT`, `RW_DASHBOARD_PORT` override the defaults.
- `single_node` splits the container memory limit between components (8 GB: compute 2.5 GiB,
  storage 1.7 GiB, reserved 1.8 GiB). It is the default subcommand of the image; `playground` is the
  same with an in-memory store.
- State (SQLite + Hummock files) lives in the `rw-data` volume; `make down` deletes it.

## What it does

| File | Shows |
|---|---|
| [`01-tables-and-source.sql`](sql/01-tables-and-source.sql) | tables with primary keys (DML), a `datagen` source (200 rows/s, built in), `FLUSH` |
| [`02-materialized-views.sql`](sql/02-materialized-views.sql) | join + aggregate MV over two tables, stream-table join MV over the source; `UPDATE`/`DELETE` on either input retract and re-add rows; asserts the MV equals the expected total (117.24) |
| [`03-emit-on-window-close.sql`](sql/03-emit-on-window-close.sql) | watermark on an append-only table, 1-minute `TUMBLE` with and without `EMIT ON WINDOW CLOSE`: the live MV shows the open window at once, the EOWC MV only after the watermark passes the window end; a late row is dropped |
| [`04-sinks.sql`](sql/04-sinks.sql) | `CREATE SINK ... INTO` a RisingWave table; native `connector = 'postgres'` upsert sink of the MV into the `postgres` container |
| [`05-catalog.sql`](sql/05-catalog.sql) | `SHOW MATERIALIZED VIEWS`, `SHOW SINKS`, `rw_streaming_parallelism`, `rw_worker_nodes` |

`make test` runs psql with `ON_ERROR_STOP=1`; the assertions are `SELECT 1 / (CASE WHEN ... THEN 1
ELSE 0 END)`, which fail with a division by zero. It then polls `sinkdb.revenue_by_region` until it
equals the MV (`sink matches MV: eu=109.99,us=52.26`).

## Results

2026-10-04, Apple M4 Pro, Docker VM aarch64 (11 CPUs, 24 GB, shared with other projects),
container capped at 4 CPUs / 8 GB. One run.

| Measured | Value |
|---|---|
| `make up` from an empty volume to healthy | 5.3 s |
| `make test` (5 files, 10 streaming jobs) | 14.4 s |
| RisingWave memory after `make test` | 318 MiB |
| `/data` after `make test` | 3.5 MB |
| Image on disk | 11.2 GB unpacked (2.52 GB compressed) |

## Known issues

- At `mem_limit: 6g` the process exits with code 133 a second after start:
  `thread 'rw-standalone-compactor' panicked at src/storage/compactor/src/server.rs:125:9:
  assertion failed: compactor_memory_limit_bytes > min_compactor_memory_limit_bytes as usize * 2`.
  8 GB works.
- With the default parallelism (4 here) the EOWC window did not close after a row past the window
  end, because a watermark downstream is the minimum over all parallel actors and the DML rows of
  an append-only table go round-robin to the actors. `03-emit-on-window-close.sql` sets
  `streaming_parallelism = 1`. With a real source each split carries its own watermark.
- `APPEND ONLY TABLE` and `EMIT ON WINDOW CLOSE` print `NOTICE: ... is currently an experimental feature`.
- Parallelism shows as `bounded(4)` for tables and `bounded(64)` for MVs: the free license caps a
  cluster at 4 RWU (4 CPU cores); see [`../README.md`](../README.md#known-issues).

## Links

- [Quick start](https://docs.risingwave.com/get-started/quickstart)
- [Emit on window close](https://docs.risingwave.com/processing/emit-on-window-close)
- [Sink into table](https://docs.risingwave.com/sql/commands/sql-create-sink-into) ·
  [Postgres sink](https://docs.risingwave.com/integrations/destinations/postgresql)
