# InfluxDB 3 Core

Website: https://www.influxdata.com/products/influxdb/ (source: https://github.com/influxdata/influxdb)

InfluxDB 3 is a rewrite in Rust on the FDAP stack (Arrow Flight, DataFusion, Arrow, Parquet). Writes are
line protocol. Queries are SQL (DataFusion) or InfluxQL. Recent data is buffered in memory and
in a WAL on the object store, and snapshots persist it as Parquet files there. Core is the
open-source (MIT/Apache-2.0) edition.

- [`v3-core-single-node/`](v3-core-single-node) — `influxdb3 serve` on MinIO (S3) with an offline admin token: line protocol over the v3/v2/v1 write APIs, SQL and InfluxQL, last/distinct value caches, a WAL and a schedule plugin in the embedded Python processing engine, and the Parquet files it writes to the bucket.

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

Line protocol over HTTP into one Core node capped at 3 CPUs / 5 GB, with MinIO (1 CPU) as the
object store (Apple M4 Pro, Docker VM aarch64, 2026-10-02), 2M rows / 10k series. Durable writes
reach 162k rows/s with 16 writers and 201k/s with 64. Each request waits for the 1 s WAL flush,
so throughput comes from concurrency. With `no_sync=true` (ack before the flush) it reaches
1.17M rows/s. One host's latest values take 0.5–1 ms from the last value cache, against
2–12 ms in SQL. A 10k-row distinct list takes 3–6 ms from the distinct value cache, against
13–200 ms with `SELECT DISTINCT`. Dumping the whole last value cache is no faster than SQL.
Full tables and method:
[`v3-core-single-node/README.md`](v3-core-single-node/README.md#benchmark).

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
- When the Docker VM disk filled up (shared with other examples), MinIO answered
  `507 Insufficient Storage`. InfluxDB then failed `create database` with `object store error:
  ... RetryError ... retries: 10 ... inner: Status { status: 507 ...` and exited (code 1) at
  startup. It recovered once space was freed and needed no repair. Keep a few GB free.
- MinIO no longer publishes community images, so the examples use Chainguard's `latest` build
  pinned by digest.
