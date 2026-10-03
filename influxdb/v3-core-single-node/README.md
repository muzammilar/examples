# InfluxDB 3 Core: single node

One InfluxDB 3 Core server (`influxdb3 serve`) on MinIO. The server keeps no local state: WAL,
catalog, snapshots and Parquet files all go to the S3 bucket `influxdb3`. HTTP API:
`localhost:8191` (`INFLUXDB_PORT`).

## Quick start

| Command | What |
|---------|------|
| `make up` | MinIO, the bucket, the admin token, then `influxdb3 serve` (healthy in ~2-8 s) |
| `make test` | [`scripts/demo.sh`](scripts/demo.sh) (below), then the bucket's objects as MinIO lists them |
| `make benchmark` | ingest + query latency, [`bench/bench.py`](bench/bench.py) (`SMOKE=1`: 200k rows) |
| `make status` | containers, `/ping`, databases |
| `make cli` | bash in the server container with `INFLUXDB3_AUTH_TOKEN` set |
| `make files` | the bucket listing only |
| `make down` | remove containers and volumes (data, token) |

## Setup

| Item | Value |
|------|-------|
| Image | `influxdb:3.12.0-core` (`INFLUXDB_VERSION`) |
| Object store | MinIO, Chainguard's free build pinned by digest (MinIO no longer publishes community images), as [`../../milvus/single-node`](../../milvus/single-node) does. Not published to the host; `minioadmin`/`minioadmin`. |
| Admin token | one-shot `init` service runs `influxdb3 create token --admin --offline`; server loads it with `--admin-token-file`. Without that flag you run `influxdb3 create token --admin` once against the running server and save the output; it cannot be shown again (`--regenerate` replaces it). |
| `--wal-files-per-snapshot=10` | default 600. Snapshots every ~10 WAL files (~10 s of writes) instead of ~10 min, so Parquet files appear while you watch. Each snapshot writes one file per table per 10-minute chunk it touches; no compactor in Core, so small files stay small. Override with `WAL_FILES_PER_SNAPSHOT` / `GEN1_DURATION`. |
| `--disable-authz=health,ping` | `/health` and `/ping` open for the healthcheck; every other endpoint needs `Authorization: Bearer <token>` |
| `--virtual-env-location` | keeps the plugin venv out of the bind-mounted `plugins/` (mounted read-only) |

## What `make test` does

[`scripts/demo.sh`](scripts/demo.sh) uses the `influxdb3` CLI and curl from the same image. It
drops and recreates database `home`, so it can be repeated.

| Step | What |
|------|------|
| 1. auth | No token: 401. Creates a named admin token with a 1 h expiry, lists tokens (`influxdb3 show tokens`), deletes it. Core has admin tokens only; database-scoped (resource) tokens are Enterprise. |
| 2. database | `home` with `--retention-period 30d` |
| 3. writes | 90 lines (`home,room=Kitchen temp=21.0,hum=35.9,co=0i <ts>`, 3 rooms, one a minute for 30 min) to `/api/v3/write_lp`, plus one line each via v1 `/write` and v2 `/api/v2/write`. Schema comes from the writes, no DDL. A mistyped field (`temp="warm"`) gets `400 partial write of line protocol occurred`; the good lines are kept. |
| 4. SQL | `/api/v3/query_sql` and `influxdb3 query` (Flight): per-room aggregates, `date_bin` 10-minute buckets, `information_schema.columns` (tags are `Dictionary(Int32, Utf8)`) |
| 5. InfluxQL | `/api/v3/query_influxql` (`MEAN`, `MAX ... GROUP BY room`) and v1 `/query` (`SHOW TAG VALUES`) |
| 6. caches | last value cache (`--key-columns room --value-columns temp,hum,co`) and distinct value cache on `room`, queried with `last_cache('home', 'home_last')` and `distinct_cache('home', 'home_rooms')` (SQL only). They fill only from writes after creation, so the demo writes one more reading per room. |
| 7. processing engine | Embedded Python, `--plugin-dir` = [`plugins/`](plugins). WAL trigger (`table:home`, [`temp_alert.py`](plugins/temp_alert.py)) writes a `home_alerts` row for every reading above `max_temp=23`. Schedule trigger (`every:5s`, [`rollup.py`](plugins/rollup.py)) aggregates the last 15 min into `home_rollup`. Plugin logs are in `system.processing_engine_logs`. `rollup` keeps running every 5 s after the demo; stop it with `influxdb3 disable trigger --database home rollup` (in `make cli`). |
| 8. Parquet | Waits for `system.parquet_files` to list the `home` files, one per 10-minute chunk (`--gen1-duration`), e.g. `node0/dbs/1/0/2026-10-03/06-10/0000000030.parquet` (`dbs/<db id>/<table id>/<date>/<chunk>/<snapshot seq>`). `make files` lists the same objects in MinIO next to `node0/wal/*.wal` (one per second with writes), the catalog and `snapshots/*.info.json`. |

## Benchmark

[`bench/bench.py`](bench/bench.py): standard library, `uv run --frozen` in
`ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`, against the running server.

1. Recreates database `bench`; creates a last value cache (key `host`) and a distinct value cache
   (`region, host`) on `cpu`.
2. Posts `ROWS` rows for `SERIES` hosts to `/api/v3/write_lp`:
   `cpu,host=host-N,region=rM usage_user=..,usage_system=..,usage_idle=..,load=..i`, one point per
   host every 10 s ending now, in time order. Requests are pre-built; `WRITERS` threads post them
   over keep-alive connections. `NO_SYNC=1` adds `no_sync=true`.
3. Waits until `count(*)` sees every row, then runs each query `ITER` times via
   `/api/v3/query_sql` (JSON).
4. Writes `results/influxdb3-single-<UTC time>.json` (gitignored).

| Knob | Default |
|------|---------|
| `ROWS` | 2,000,000 |
| `SERIES` | 10,000 |
| `BATCH` | 10,000 lines (~1.1 MB) |
| `WRITERS` | 16 |
| `ITER` | 30 |
| `BENCH_CPUS` / `BENCH_MEM` | 4 / `6g` total, applied by [`bench/limits.sh`](bench/limits.sh) via `docker update` and restored afterwards: MinIO fixed 1 CPU / 1 GB, `influxdb3` 3 CPUs / 5 GB |
| `BENCH_CLIENT_CPUS` | 2 |

### Sample results

2026-10-02, Docker Desktop on Apple M4 Pro (VM: 11 CPUs, 24.4 GiB, aarch64, native images),
InfluxDB 3 Core 3.12.0, limits above, 2M rows / 10k series, 10k lines per request.

| writers | ack | rows/s | MB/s of LP | request p50 | request p99 |
|--------:|-----|-------:|-----------:|------------:|------------:|
| 4 | after WAL flush (default) | 40,342 | 4.5 | 996 ms | 1,320 ms |
| 16 (default) | after WAL flush | 161,920 | 17.9 | 971 ms | 1,362 ms |
| 64 | after WAL flush | 200,806 | 22.2 | 2,520 ms | 4,287 ms |
| 16 | `no_sync=true` | 1,165,181 | 128.8 | 114 ms | 284 ms |

- A durable write returns after the next WAL flush to the object store (`--wal-flush-interval`,
  1 s). Every request waits ~1 s, so throughput comes from concurrency: 4 → 16 writers is 4x the
  rows/s at the same latency. At 64 writers the server's 3 CPUs are the limit.
- `no_sync=true` acks once the request is parsed and validated: 7x faster, but a crash loses the
  unflushed rows. Rows became queryable 1.7 s after the last ack; in earlier runs, queries right
  after the acks saw part of the data.

Queries, default run (16 writers), 30 runs each, ms:

| query | rows | p50 | p99 |
|-------|-----:|----:|----:|
| `count(*)` over 2M rows | 1 | 226 | 574 |
| `avg(usage_user)` by region, last 5 min | 16 | 95 | 172 |
| 1 host, all 200 points | 200 | 12.3 | 28 |
| 1 host latest: `ORDER BY time DESC LIMIT 1` | 1 | 7.4 | 22 |
| 1 host latest: `last_cache()` | 1 | **0.97** | 4.9 |
| all 10k hosts latest: `last_value(... ORDER BY time) GROUP BY host` | 10,000 | 156 | 262 |
| all 10k hosts latest: `last_cache()` | 10,000 | 177 | 443 |
| distinct (region, host): `SELECT DISTINCT` | 10,000 | 202 | 434 |
| distinct (region, host): `distinct_cache()` | 10,000 | **6.1** | 25 |

- Keyed lookups are where the caches pay off: one host's latest under 1 ms from the last value
  cache vs 2–12 ms in SQL across runs; the 10k-row distinct list 3–6 ms from the distinct value
  cache vs 13–200 ms with `SELECT DISTINCT`.
- Dumping the whole last value cache (10k keys, no key predicate) was never faster than SQL
  `GROUP BY` (177–426 ms vs 17–194 ms across four runs). Query it by key.
- Other agents shared the Docker VM, so scan-type queries varied 2–10x between runs (`count(*)`
  p50 from 0.8 to 226 ms). Compare within a run; repeat before trusting small differences.

## Known issues

Shared with [`iot-fleet/`](../iot-fleet): see [InfluxDB 3 Core known issues](../README.md#known-issues).
