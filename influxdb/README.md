# InfluxDB

Website: https://www.influxdata.com/products/influxdb/ (source: https://github.com/influxdata/influxdb)

InfluxDB 3 is a rewrite in Rust on the FDAP stack (Arrow Flight, DataFusion, Arrow, Parquet). Writes are
line protocol. Queries are SQL (DataFusion) or InfluxQL. Recent data is buffered in memory and
in a WAL on the object store, and snapshots persist it as Parquet files there. Core is the
open-source (MIT/Apache-2.0) edition, and Enterprise is the closed-source, licensed one.
InfluxDB 1.x (TSM engine, InfluxQL) is the previous generation.

- [`v3-core-single-node/`](v3-core-single-node) — InfluxDB 3 Core: `influxdb3 serve` on MinIO (S3) with an offline admin token: line protocol over the v3/v2/v1 write APIs, SQL and InfluxQL, last/distinct value caches, a WAL and a schedule plugin in the embedded Python processing engine, and the Parquet files it writes to the bucket.
- [`iot-fleet/`](iot-fleet) — InfluxDB 3 Core, an IoT fleet in Rust (reqwest against the HTTP API): ingest at 1k–1M series, dashboard queries through the last/distinct value caches vs plain SQL, a week of history in Parquet, and the query file limit that bounds it in Core.
- [`v1-oss-cluster/`](v1-oss-cluster) — InfluxDB **1.8** clustering from the third-party MIT fork `chengshiwen/influxdb-cluster` (dormant since 2024-09): 3 meta + 2 data nodes, replication factor 2, write consistency levels, `make failover` with hinted handoff.

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

- **InfluxDB 3 Enterprise cluster**: not included, because Enterprise does not start without a
  license and the free ones need a person to verify an email address. The `home` license
  (2 cores, no expiry, non-commercial) is single node only. A cluster needs `trial` (30 days,
  256 cores, non-commercial) or a commercial license
  ([license docs](https://docs.influxdata.com/influxdb3/enterprise/admin/license/)). To set one up
  yourself with Docker Compose:
  1. Run MinIO and create a bucket, as [`v3-core-single-node/`](v3-core-single-node) does.
  2. Start one node from the `influxdb:<version>-enterprise` image (pin the version) with
     `influxdb3 serve --node-id ingest-1 --cluster-id cluster0 --mode ingest --object-store s3`
     and the bucket settings, plus `INFLUXDB3_LICENSE_EMAIL=<you>` and
     `INFLUXDB3_LICENSE_TYPE=trial`. Click the link in the email from InfluxData. The license is
     then stored in the bucket at `cluster0/trial_or_home_license`, so later starts skip the
     email as long as the bucket is kept.
  3. Add more nodes with the same `--cluster-id` and bucket and their own `--node-id`: a second
     `--mode ingest`, one or more `--mode query` read replicas, and exactly one `--mode compact`.
     Give every node the same admin token (`--admin-token-file`).
  4. Write to any ingest node and query any query node.
  Layout and flags: the [Enterprise multi-server guide](https://docs.influxdata.com/influxdb3/enterprise/get-started/multi-server/).
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

**v3 Core IoT fleet example** ([`iot-fleet`](iot-fleet/README.md#sample-output),
`make run`): Rust HTTP client with 16 writers, no CPU or memory caps (same machine, 2026-10-02).
Ingest does not slow down as cardinality grows. At 1k, 10k, 100k and 1M distinct devices, 1M
rows per level ran at 144–158k rows/s durable and 2.7–3.0M rows/s with `no_sync`. Over 100k
devices the last value cache answers one device's latest reading in 0.7 ms, against 4.4 ms in
SQL, and the distinct value cache lists 1,000 sites in 0.9 ms. A fleet-wide predicate over the
whole cache took 1.2 s, 5–80x slower than SQL. 7 days × 1,000 devices (2M rows) became 1,009
Parquet files, 18 MB, 9 bytes/row. Queries over up to 2 days take 5–10 ms. The 7-day query fails
on Core's 432-file limit; with `--query-file-limit=2500` it takes 22 ms.

**1.8 OSS cluster fork** ([`v1-oss-cluster`](v1-oss-cluster/README.md#benchmark), `make
benchmark`): Python HTTP writer, 2M rows / 10k series into a replication-factor-2 database, two
data nodes with 1.5 CPUs / 2.25 GB each and three meta nodes with 0.33 CPU each (Apple M4 Pro,
Docker VM aarch64, 2026-10-03). Throughput was 202k rows/s with 16 writers at `consistency=one`,
201k/s at `consistency=all` and 252k/s with 4 writers. Every row is stored on both nodes. With
`one`, the second replica trailed the acks by up to 60k rows and caught up 1.3–1.8 s after the
last one. Through the TSM index, `last()` for one series takes 0.14 ms and 200 points of one
series 0.44 ms. `count` over 2M points takes 310 ms and `last() GROUP BY host` over 10k series
172 ms.

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

InfluxDB 1.x cluster fork (`chengshiwen/influxdb:1.8.11-c1.2.0`, 2026-10-03):

- **`chengshiwen/influxdb-cluster` is a dormant third-party project.** It is not from
  InfluxData, and its last release, image and commit (`v1.8.11-c1.2.0`) are from 2024-09-08. It
  is based on InfluxDB 1.8, which is itself in maintenance.
- After `make failover` (restarting data nodes), `DROP DATABASE fleet` + `CREATE DATABASE fleet`
  twice left the replicas **diverged**: data-1 counted 60 rows, data-2 122 (62/124 the first
  time). The recreated database reused the dropped one's shard id, and the restarted node still
  had the old data under it. A third try got a fresh shard id and matched. `make down && make
  up` starts clean.
- A `consistency=all` write with one replica down answers `500 {"error":"partial write"}` but is
  not rolled back. The point is stored on the live node and queued for the other.
- `/query` with `Accept: application/csv` prints the integer `replicaN` column of `SHOW
  RETENTION POLICIES` as the literal text `replicaN`. JSON is correct.
- Right after the join, `influxd-ctl show` lists some nodes with an empty version column. It
  fills in after a few seconds.
