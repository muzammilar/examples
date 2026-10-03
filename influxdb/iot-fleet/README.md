# InfluxDB 3 Core: IoT fleet (Rust client)

An IoT sensor fleet on one InfluxDB 3 Core node backed by MinIO, driven by [`app/`](app), a Rust
program: high-cardinality ingest, "latest value" dashboards from the in-memory caches, history as
Parquet in object storage, and Core's limit on how much of it one query may read. HTTP API:
`localhost:8192` (`INFLUXDB_PORT`).

## Quick start

| Command | What |
|---------|------|
| `make up` | MinIO, the bucket, an offline admin token, `influxdb3 serve` |
| `make run` | build the app image (first time ~1 min) and run it (~2 min) |
| `make build` | build the app image only |
| `make files` | object store usage per prefix: WAL, Parquet (`dbs/`), snapshots, catalog |
| `make status` | containers, `/ping`, CPU and memory |
| `make down` | remove containers, volumes and the built image |

Override sizes with `CARDINALITIES`, `ROWS_PER_LEVEL`, `WRITERS`, `BATCH`, `FLEET_DEVICES`,
`FLEET_POINTS`, `HISTORY_DEVICES`, `HISTORY_DAYS`, `HISTORY_STEP_S`, `ITER`, e.g.
`make run CARDINALITIES=1000,10000 ROWS_PER_LEVEL=100000` (make passes them to compose).

## What `make run` does

Recreates database `iot` each time, so it can be repeated.

| Phase | What |
|-------|------|
| 1. Cardinality ladder | For 1k, 10k, 100k and 1M distinct devices: 1M rows of `<table>,site=site-NNNN,device_id=dev-NNNNNNN,model=mN temp=..,humidity=..,battery=..i,rssi=..i` (1,000 points per device at 1k, 1 point each at 1M). Each level written twice: durable (ack waits for the WAL flush) and `no_sync=true` (ack first; then polls until `count(*)` returns every row). 16 writers, 10,000 lines per request. `count(*)` and `count(DISTINCT device_id)` must match exactly. Reports server heap (jemalloc `resident` from `/metrics`). |
| 2. Dashboard | Table `fleet` (tags `site`, `device_id`, `model`), last value cache keyed `(site, device_id)`, distinct value cache on `site`. Writes 10 readings for each of 100,000 devices in 1,000 sites. Four questions, each in plain SQL and via a cache, 30 times; both must return the same rows: one device's latest reading; latest reading of every device in one site; devices with battery < 20 now; list of sites. |
| 3. History | 1,000 devices × 7 days at 5-minute resolution = 2,016,000 rows with past timestamps, oldest first. Polls `system.parquet_files` until every row is in Parquet (writing a heartbeat row each second to keep the WAL moving), reports file count, bytes and compression vs line protocol. Queries: one device over the last 6 h and over a 6 h window 6 days ago, fleet hourly average over 24 h, daily averages over 2 days and over all 7. |

## Setup

| Item | Value |
|------|-------|
| Server | `influxdb:3.12.0-core` (`INFLUXDB_VERSION`), Chainguard MinIO pinned by digest |
| `--wal-files-per-snapshot=10` | snapshots every ~10 s of writes (default ~10 min) |
| `--query-file-limit=${QUERY_FILE_LIMIT:-432}` | the Core default, made explicit |
| Compose project | `influxdb3-iot`, containers `influxdb3-iot` and `influxdb3-iot-minio`, so it runs next to [`../v3-core-single-node`](../v3-core-single-node) |
| App image | [`app/Dockerfile`](app/Dockerfile): built in `rust:1.90-slim-bookworm`, binary copied into `debian:bookworm-slim` (no TLS, so no OpenSSL). No host Rust needed. |

### Rust client

InfluxDB 3 has no official Rust client; InfluxData's documented v3 client libraries are Go,
Python, Java, C# and JavaScript. The community crate
[`influxdb3_client`](https://github.com/InfluxCommunity/influxdb3-rust) (0.3, InfluxCommunity, not
supported by InfluxData) wraps writes and Flight SQL queries. This app calls the HTTP API
directly with `reqwest` + `serde_json` on tokio ([`Cargo.toml`](app/Cargo.toml)):

| Endpoint | Use |
|----------|-----|
| `POST /api/v3/write_lp?db=iot&precision=nanosecond[&no_sync=true]` | line protocol body |
| `POST /api/v3/query_sql` | `{"db", "q", "format": "json"}` |
| `POST /api/v3/configure/{database,table,last_cache,distinct_cache}` | schema and caches |
| `DELETE /api/v3/configure/database?hard_delete_at=now` | drop database |

## Sample output

2026-10-02, fresh `make up` then `make run` (defaults), Docker Desktop on Apple M4 Pro (VM: 11
CPUs, 24.4 GiB, aarch64), InfluxDB 3 Core 3.12.0, no CPU or memory caps:

```text
1. ingest vs series cardinality: 1,000,000 rows per level, 16 writers, 10,000 lines per request
   durable = ack after the WAL flush to the object store; no_sync = ack before it (then wait until all rows are queryable)
      series        rows   durable r/s   p50 ms   no_sync r/s  queryable heap MiB  check
       1,000   1,000,000       157,577     1005     2,780,343      1.16s      881  1,000,000 rows, 1,000 distinct device_id in each table
      10,000   1,000,000       143,564     1007     2,973,649      1.25s     1032  1,000,000 rows, 10,000 distinct device_id in each table
     100,000   1,000,000       148,431      992     2,900,235      1.29s     1346  1,000,000 rows, 100,000 distinct device_id in each table
   1,000,000   1,000,000       150,598     1001     2,699,143      1.64s     1835  1,000,000 rows, 1,000,000 distinct device_id in each table

2. dashboard on `fleet`: 100,000 devices in 1,000 sites, 10 readings each; last value cache keyed (site, device_id)
   wrote 1,000,000 rows in 6.22 s = 160,762 rows/s (the caches fill on the write path)
   query                             rows    SQL p50    SQL p99  cache p50  cache p99  speedup  same answer
   1 device, latest reading             1     4.38ms     5.95ms     0.68ms     0.79ms     6.4x  yes
   1 site, latest per device          100     6.87ms     8.06ms     1.96ms     2.45ms     3.5x  yes
   whole fleet: battery < 20 now        1   227.88ms   255.90ms  1171.22ms  1332.04ms     0.2x  yes
   list of sites                     1000   235.67ms   290.87ms     0.87ms     1.22ms   271.6x  yes

3. history: 1,000 devices x 7 days at 300 s = 2,016,000 rows, written oldest first
   wrote 2,016,000 rows (244 MB of line protocol) in 12.26 s = 164,395 rows/s
   persisted after 22 s: 2,016,000 of 2,016,000 rows in 1009 Parquet files, 18.2 MB (13.4x smaller than the line protocol, 9.0 bytes/row)
   query                               rows  first run       p50       p99
   1 device, last 6 h                    71      7.8ms     4.9ms     6.1ms
   1 device, 6 h window 6 days ago       72      5.6ms     5.1ms     5.8ms
   fleet hourly avg, last 24 h           24      6.6ms     5.5ms     6.8ms
   fleet daily avg, last 2 days           3      9.6ms    10.4ms    18.3ms
   fleet daily avg, all days         error: /api/v3/query_sql -> 500 Internal Server Error: External error: Query would scan 432 Parquet files, exceeding the file limit. InfluxDB 3 Core caps file access to prevent performance degradation and memory issues. Use a narrower time range, or increase the limit with --query-file-limit (this may cause slower queries or instability).
   [... followed by an upsell paragraph for InfluxDB 3 Enterprise ...]

   Parquet in the object store (files / size): ladder_1k 3 / 4.1 MB, ladder_1k_ns 3 / 4.1 MB, ladder_10k 1 / 4.0 MB, ladder_10k_ns 1 / 4.0 MB, ladder_100k 1 / 4.2 MB, ladder_100k_ns 1 / 4.2 MB, ladder_1m 1 / 5.7 MB, ladder_1m_ns 1 / 5.7 MB, fleet 2 / 4.6 MB, history 1009 / 18.2 MB
```

- `QUERY_FILE_LIMIT=2500 make up` (server restarts with the higher limit, bucket kept): the 7-day
  daily average reads all 1,009 files, 8 rows, 20.8 ms first run, 22.1 ms p50. Other history
  queries unchanged (4.5–10 ms).
- Afterwards `make files` showed 651 MiB of WAL vs 56 MiB of Parquet in the bucket. The server
  keeps the last 300 snapshotted WAL files (`--snapshotted-wal-files-to-keep`), so right after a
  bulk load the WAL takes most of the space. Server container memory: 2.4 GiB.
- Run to run: an earlier run on the same setup had faster plain-SQL dashboard queries (whole-fleet
  battery 14–16 ms, list of sites 4–13 ms); the cache side was the same (0.7 ms, ~1.2–1.4 s,
  ~1 ms). Other examples shared the Docker VM. One earlier run hit a full VM disk mid-load (MinIO
  `507 Insufficient Storage`): the server retried the WAL PUT, pending writes stalled up to 128 s,
  then all completed with nothing lost.

## Findings

| Topic | Result |
|-------|--------|
| Cardinality | No series index (InfluxDB 1.x has one; its `max-series-per-database` defaults to 1M). 1M distinct `device_id`s ingest at the same rate as 1k: ~150k rows/s durable with 16 writers, 2.7–3.0M rows/s `no_sync`. Heap grew 0.9 → 1.8 GiB while 8M rows accumulated over the four levels. |
| Durability | A durable write is acked after the next WAL flush (1 s), so every request takes ~1 s and throughput comes from concurrency. `no_sync` acks before the flush; rows were queryable 1.2–1.6 s after the last ack, and a crash in between loses them. |
| Caches | Last value cache: one device's latest 0.7 ms vs 3–4 ms SQL; one site (key prefix) 2 ms vs 7 ms. Distinct value cache: 1,000 sites under 1 ms where `SELECT DISTINCT` scans 1M rows. No key predicate ("battery < 20 anywhere") walks all 100k entries: over 1 s, 5–80x slower than SQL. Use for keyed lookups, not fleet-wide scans. |
| History | 2M rows → 18 MB Parquet, 9 bytes/row, 13x smaller than line protocol. No separate cold tier: a 6 h window from 6 days ago (5 ms) was as fast as the last 6 h (MinIO on the same machine, small files). |
| Core limit | No compaction: every 10-minute chunk stays its own file (1,009 files for 7 days of one table); a query may open at most 432 by default (72 h). Raising `--query-file-limit` works at this size (all 7 days in 22 ms); the error text warns it "may cause slower queries or instability". Compaction is an InfluxDB 3 Enterprise feature. |

## Known issues

Shared with [`v3-core-single-node/`](../v3-core-single-node): see
[InfluxDB 3 Core known issues](../README.md#known-issues) (432-file query limit, `no_sync`
visibility lag, cache scans without a key, `507 Insufficient Storage` on a full disk).
