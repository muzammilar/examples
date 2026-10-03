# InfluxDB

Website: https://www.influxdata.com/products/influxdb/ (source: https://github.com/influxdata/influxdb)

InfluxDB 3 is a rewrite in Rust on the FDAP stack (Arrow Flight, DataFusion, Arrow, Parquet). Writes are
line protocol. Queries are SQL (DataFusion) or InfluxQL. Recent data is buffered in memory and
in a WAL on the object store, and snapshots persist it as Parquet files there. Core is the
open-source (MIT/Apache-2.0) edition, and Enterprise is the closed-source, licensed one.
InfluxDB 1.x (TSM engine, InfluxQL) is the previous generation.

- [`v3-core-single-node/`](v3-core-single-node) — InfluxDB 3 Core: `influxdb3 serve` on MinIO (S3) with an offline admin token: line protocol over the v3/v2/v1 write APIs, SQL and InfluxQL, last/distinct value caches, a WAL and a schedule plugin in the embedded Python processing engine, and the Parquet files it writes to the bucket.

## Clusters

**InfluxDB 3 Core runs on a single node only.** Multi-node clusters, high availability, read
replicas and the compactor are InfluxDB 3 Enterprise features. In an Enterprise cluster, nodes
run in `ingest`/`query`/`compact`/`process` modes, share one object store and one
`--cluster-id`, and only one node may compact. The docs say so on the
[Core overview](https://docs.influxdata.com/influxdb3/core/), which lists HA and read replicas
under Enterprise, and in the
[Enterprise multi-server guide](https://docs.influxdata.com/influxdb3/enterprise/get-started/multi-server/).
Core 3.12 has no compactor, so its Parquet files stay at the 10-minute gen1 size, and one query
may read at most `--query-file-limit` files (432 by default, 72 h of one table).

- **InfluxDB 3 Enterprise cluster**: `v3-enterprise-docker-compose-cluster/` (branch
  `influxdb3-enterprise-docker-compose-cluster`). **Not run: blocked on a license.** Enterprise
  will not start without a license. The free trial (30 days, multi-node, non-commercial) and
  at-home (2 cores, single node only) licenses both need an email address and a click on a
  verification link, which nobody did for these examples. What ran: the images pull, MinIO and
  the token come up, and every node reaches the bucket and exits with `License management
  error: No interactive TTY detected. Cannot prompt for email.`
- **Open-source cluster**: only for InfluxDB 1.x, through the third-party fork
  `chengshiwen/influxdb-cluster`, in `v1-oss-cluster/` (branch `influxdb1-oss-cluster`). It ran
  end to end.

## Benchmark

**v3 Core single node** ([`v3-core-single-node`](v3-core-single-node/README.md#benchmark),
`make benchmark`): Python HTTP writer, 2M rows / 10k series, server capped at 3 CPUs / 5 GB,
MinIO at 1 CPU (Apple M4 Pro, Docker VM aarch64, 2026-10-02). Durable writes reach 162k rows/s
with 16 writers and 201k/s with 64. Each request waits for the 1 s WAL flush, so throughput comes
from concurrency. With `no_sync=true` (ack before the flush) it reaches 1.17M rows/s. One host's
latest values take 0.5–1 ms from the last value cache, against 2–12 ms in SQL. A 10k-row
distinct list takes 3–6 ms from the distinct value cache, against 13–200 ms with `SELECT
DISTINCT`.

## Known issues

InfluxDB 3 Core is actively developed: 3.12.0 is from 2026-10, with several minor releases since
3.10 (June 2026) and patch releases on two lines at once. Seen with `influxdb:3.12.0-core`
(2026-10-02):

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
- A last value cache query without a key predicate is slow. Reading all 10k cached hosts took
  177–426 ms, against 17–194 ms for the SQL `GROUP BY`. Counting 100k cached devices by a value
  column took 1.2–1.4 s, against 14–228 ms in SQL. The caches pay off only for keyed lookups.
- `SELECT * FROM system.parquet_files` without a `WHERE table_name = ...` lists every file once
  per table with the same table id, including tables in other databases and dropped tables that
  the catalog keeps as `home-20261003T061145`. Filtered by `table_name` the listing is correct, so
  the examples always filter.
- `no_sync=true` writes are acked before they are queryable. A `count(*)` right after the last
  ack saw part of 2M rows, and all of them 1.2–5 s later.
- Core does not compact Parquet files, and a query that would open more than
  `--query-file-limit` files (432) fails with `Query would scan 432 Parquet files, exceeding the
  file limit. InfluxDB 3 Core caps file access ...`, followed by an upsell paragraph for
  Enterprise. Raising the limit works for 1,009 small files.
- When the shared Docker VM disk filled up, MinIO answered `507 Insufficient Storage`. InfluxDB
  failed `create database` with `object store error: ... retries: 10 ... inner: Status { status:
  507 ...`, and at startup it exited with code 1. During a bulk load it logged `ERROR
  influxdb3_wal::object_store: error writing wal file to object store ... 507 Insufficient
  Storage` and kept retrying. Write requests stalled for up to 128 s, then completed once space
  was back, with no rows lost. Keep a few GB free.
- MinIO no longer publishes community images, so the examples use Chainguard's `latest` build
  pinned by digest.
