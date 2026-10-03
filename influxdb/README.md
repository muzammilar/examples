# InfluxDB 3 Core

Website: https://www.influxdata.com/products/influxdb/ (source: https://github.com/influxdata/influxdb)

InfluxDB 3 is a rewrite in Rust on the FDAP stack (Arrow Flight, DataFusion, Arrow, Parquet). Writes are
line protocol. Queries are SQL (DataFusion) or InfluxQL. Recent data is buffered in memory and
in a WAL on the object store, and snapshots persist it as Parquet files there. Core is the
open-source (MIT/Apache-2.0) edition.

- [`v3-core-single-node/`](v3-core-single-node) — `influxdb3 serve` on MinIO (S3) with an offline admin token: line protocol over the v3/v2/v1 write APIs, SQL and InfluxQL, last/distinct value caches, a WAL and a schedule plugin in the embedded Python processing engine, and the Parquet files it writes to the bucket.
- [`iot-fleet-showcase/`](iot-fleet-showcase) — an IoT fleet in Rust (reqwest against the HTTP API): ingest at 1k–1M series, dashboard queries through the last/distinct value caches vs plain SQL, a week of history in Parquet, and the query file limit that bounds it in Core.

## No cluster example

Core runs as a single node only. High availability, read replicas, multi-node clusters (nodes in
`ingest`/`query`/`compact`/`process` modes sharing one object store and `--cluster-id`), and the
compactor all belong to InfluxDB 3 Enterprise, as of 3.12 (2026-10). Core has no compactor, so its
Parquet files stay at the 10-minute gen1 size. A query may touch at most `--query-file-limit`
files (432 by default, i.e. 72 hours of one table at the default gen1 duration). Enterprise is
closed source. It has a free 30-day trial license and a free at-home license
(non-commercial, 2 cores, single node), both activated by email verification; see
[the license docs](https://docs.influxdata.com/influxdb3/enterprise/admin/license/). Not used
here.

## Benchmark

**Single node** ([`v3-core-single-node`](v3-core-single-node/README.md#benchmark), `make
benchmark`): Python HTTP writer, 2M rows / 10k series, server capped at 3 CPUs / 5 GB, MinIO at
1 CPU (Apple M4 Pro, Docker VM aarch64, 2026-10-02). Durable writes reach 162k rows/s with 16
writers and 201k/s with 64. Each request waits for the 1 s WAL flush, so throughput comes from
concurrency. With `no_sync=true` (ack before the flush) it reaches 1.17M rows/s. One host's
latest values take 0.5–1 ms from the last value cache, against 2–12 ms in SQL. A 10k-row
distinct list takes 3–6 ms from the distinct value cache, against 13–200 ms with `SELECT
DISTINCT`.

**IoT fleet showcase** ([`iot-fleet-showcase`](iot-fleet-showcase/README.md#sample-output),
`make run`): Rust HTTP client with 16 writers, no CPU or memory caps (same machine, 2026-10-02).
Ingest does not slow down as cardinality grows. At 1k, 10k, 100k and 1M distinct devices, 1M
rows per level ran at 144–158k rows/s durable and 2.7–3.0M rows/s with `no_sync`. Over 100k
devices the last value cache answers one device's latest reading in 0.7 ms, against 4.4 ms in
SQL, and the distinct value cache lists 1,000 sites in 0.9 ms. A fleet-wide predicate over the
whole cache took 1.2 s, 5–80x slower than SQL. 7 days × 1,000 devices (2M rows) became 1,009
Parquet files, 18 MB, 9 bytes/row. Queries over up to 2 days take 5–10 ms. The 7-day query fails
on Core's 432-file limit; with `--query-file-limit=2500` it takes 22 ms.

## Known issues

InfluxDB 3 Core is actively developed: 3.12.0 is from 2026-10, with several minor releases since
3.10 (June 2026) and patch releases on two lines at once. Seen while building these examples
with `influxdb:3.12.0-core` (2026-10-02):

- `docker run influxdb:3-core --version` fails with `error: unexpected argument '--version'
  found`. The image's entrypoint turns any argument that starts with `-` into
  `influxdb3 serve ...`. Use `docker run --rm --entrypoint influxdb3 influxdb:3-core --version`.
- With auth on, even `/ping` and `/health` need a token. A curl healthcheck stays unhealthy
  and the log fills with `ERROR influxdb3_server::http: cannot authenticate token
  e=MissingToken path="/ping"`. Fixed with `--disable-authz=health,ping`.
- The processing engine creates its Python venv in `--plugin-dir`. With `./plugins` bind-mounted,
  a `.venv/` appeared in the repo. Fixed with `--virtual-env-location=/home/influxdb3/.venv` and
  a read-only mount.
- Last value and distinct value caches start empty. They are not filled from data already
  in the table, only from writes after `create last_cache` / `create distinct_cache`.
- `SELECT * FROM system.parquet_files` without a `WHERE table_name = ...` lists every file once
  per table with the same table id, including tables in other databases and dropped tables that
  the catalog keeps as `home-20261003T061145`. Filtered by `table_name` the listing is correct, so
  the examples always filter.
- `no_sync=true` writes are acked before they are queryable. A `count(*)` right after the last
  ack saw part of 2M rows, and all of them 1.7–5 s later.
- During a bulk load the shared VM disk filled and MinIO answered `507 Insufficient Storage` to
  WAL PUTs (`ERROR influxdb3_wal::object_store: error writing wal file to object store ...
  507 Insufficient Storage`). The server kept retrying, and the write requests stalled for up to
  128 s, then completed once space was back, with no rows lost. The same condition failed
  `create database` with `object store error: ... retries: 10 ... inner: Status { status: 507 ...`
  and, at startup, made the server exit with code 1. Keep a few GB free.
- A last value cache query without a key predicate is slow. Counting 100k cached devices by a
  value column took 1.2–1.4 s, where the equivalent SQL over the table took 14–228 ms. The
  caches pay off only for keyed lookups.
- Core does not compact Parquet files, and a query that would open more than
  `--query-file-limit` files (432) fails with `Query would scan 432 Parquet files, exceeding the
  file limit. InfluxDB 3 Core caps file access ...`, followed by an upsell paragraph for
  Enterprise. The iot-fleet showcase hits this with 7 days of one table. Raising the limit works
  at that size.
- MinIO no longer publishes community images, so the examples use Chainguard's `latest` build
  pinned by digest.
