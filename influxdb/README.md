# InfluxDB

Website: https://www.influxdata.com/products/influxdb/ · Source: https://github.com/influxdata/influxdb

| Version | Engine | Writes | Queries | License |
|---------|--------|--------|---------|---------|
| InfluxDB 3 Core | Rust, FDAP stack (Arrow Flight, DataFusion, Arrow, Parquet). Recent data in memory + a WAL on the object store; snapshots persist it as Parquet there. | line protocol | SQL (DataFusion), InfluxQL | MIT/Apache-2.0 |
| InfluxDB 3 Enterprise | same, plus clustering and compaction | line protocol | SQL, InfluxQL | closed source, licensed |
| InfluxDB 1.x | TSM (previous generation) | line protocol | InfluxQL | open source; clustering only in closed-source Enterprise 1.x |

| Folder | What |
|--------|------|
| [`v3-core-single-node/`](v3-core-single-node) | InfluxDB 3 Core `influxdb3 serve` on MinIO (S3) with an offline admin token: line protocol over the v3/v2/v1 write APIs, SQL and InfluxQL, last/distinct value caches, WAL and schedule plugins in the embedded Python processing engine, the Parquet files in the bucket. |
| [`iot-fleet/`](iot-fleet) | InfluxDB 3 Core with an IoT fleet in Rust (reqwest against the HTTP API): ingest at 1k–1M series, dashboard queries through the caches vs plain SQL, a week of history in Parquet, and Core's query file limit. |
| [`v1-oss-cluster/`](v1-oss-cluster) | InfluxDB **1.8** clustering from the third-party MIT fork `chengshiwen/influxdb-cluster` (dormant since 2024-09): 3 meta + 2 data nodes, replication factor 2, write consistency levels, `make failover` with hinted handoff. |

## Clusters

**InfluxDB 3 Core is single node only.** Multi-node clusters, HA, read replicas and the compactor
are Enterprise features ([Core overview](https://docs.influxdata.com/influxdb3/core/),
[Enterprise multi-server guide](https://docs.influxdata.com/influxdb3/enterprise/get-started/multi-server/)).
Enterprise nodes run in `ingest`/`query`/`compact`/`process` modes, share one object store and one
`--cluster-id`, and only one node may compact. Core 3.12 has no compactor: Parquet files stay at
the 10-minute gen1 size, and one query may read at most `--query-file-limit` files (432 by
default, 72 h of one table).

| Cluster option | Status |
|----------------|--------|
| InfluxDB 3 Enterprise | Not included. Enterprise does not start without a license, and the free ones need a person to verify an email address. `home` (2 cores, no expiry, non-commercial) is single node only; a cluster needs `trial` (30 days, 256 cores, non-commercial) or a commercial license ([license docs](https://docs.influxdata.com/influxdb3/enterprise/admin/license/)). Manual steps below. |
| Open-source | InfluxDB 1.x only, via the third-party fork `chengshiwen/influxdb-cluster`, in [`v1-oss-cluster/`](v1-oss-cluster) (branch `influxdb1-oss-cluster`). Ran end to end. |

Enterprise cluster with Docker Compose (layout and flags: the
[multi-server guide](https://docs.influxdata.com/influxdb3/enterprise/get-started/multi-server/)):

1. Run MinIO and create a bucket, as [`v3-core-single-node/`](v3-core-single-node) does.
2. Start one node from `influxdb:<version>-enterprise` (pin the version) with
   `influxdb3 serve --node-id ingest-1 --cluster-id cluster0 --mode ingest --object-store s3`, the
   bucket settings, `INFLUXDB3_LICENSE_EMAIL=<you>` and `INFLUXDB3_LICENSE_TYPE=trial`. Click the
   link in the email from InfluxData. The license is stored in the bucket at
   `cluster0/trial_or_home_license`, so later starts skip the email while the bucket is kept.
3. Add nodes with the same `--cluster-id` and bucket and their own `--node-id`: a second
   `--mode ingest`, one or more `--mode query` read replicas, exactly one `--mode compact`. Give
   every node the same admin token (`--admin-token-file`).
4. Write to any ingest node, query any query node.

## Benchmark summary

Apple M4 Pro, Docker Desktop VM (aarch64). Details in each example.

| Example | Setup | Ingest | Reads |
|---------|-------|--------|-------|
| [v3 Core single node](v3-core-single-node/README.md#benchmark) (`make benchmark`, 2026-10-02) | Python HTTP writer, 2M rows / 10k series; server 3 CPUs / 5 GB, MinIO 1 CPU | durable: 162k rows/s (16 writers), 201k/s (64); `no_sync=true`: 1.17M rows/s. Each request waits for the 1 s WAL flush, so throughput comes from concurrency. | one host's latest: 0.5–1 ms last value cache vs 2–12 ms SQL; 10k-row distinct list: 3–6 ms distinct value cache vs 13–200 ms `SELECT DISTINCT` |
| [v3 Core IoT fleet](iot-fleet/README.md#sample-output) (`make run`, 2026-10-02) | Rust HTTP client, 16 writers, no CPU/memory caps | 1M rows per level at 1k/10k/100k/1M devices: 144–158k rows/s durable, 2.7–3.0M rows/s `no_sync`; no slowdown with cardinality | over 100k devices: one device's latest 0.7 ms cache vs 4.4 ms SQL; 1,000 sites in 0.9 ms from the distinct value cache; fleet-wide predicate over the cache 1.2 s (5–80x slower than SQL). 7 days × 1,000 devices (2M rows) = 1,009 Parquet files, 18 MB, 9 bytes/row; queries up to 2 days 5–10 ms; 7 days fails on the 432-file limit, 22 ms with `--query-file-limit=2500` |
| [1.8 OSS cluster fork](v1-oss-cluster/README.md#benchmark) (`make benchmark`, 2026-10-03) | Python HTTP writer, 2M rows / 10k series, RF 2; data nodes 1.5 CPUs / 2.25 GB each, meta nodes 0.33 CPU each | 202k rows/s (16 writers, `consistency=one`), 201k/s (`all`), 252k/s (4 writers). With `one`, the second replica trailed by up to 60k rows and caught up 1.3–1.8 s after the last ack. | TSM index: `last()` one series 0.14 ms, 200 points of one series 0.44 ms; `count` over 2M points 310 ms; `last() GROUP BY host` over 10k series 172 ms |

## Known issues

InfluxDB 3 Core is actively developed: 3.12.0 is from 2026-10, with several minor releases since
3.10 (June 2026) and patch releases on two lines at once. Seen with `influxdb:3.12.0-core`
(2026-10-02):

| Symptom | Cause | Workaround |
|---------|-------|------------|
| `docker run influxdb:3-core --version` fails: `error: unexpected argument '--version' found` | The entrypoint turns any argument starting with `-` into `influxdb3 serve ...` | `docker run --rm --entrypoint influxdb3 influxdb:3-core --version` |
| curl healthcheck stays unhealthy; log fills with `ERROR influxdb3_server::http: cannot authenticate token e=MissingToken path="/ping"` | With auth on, `/ping` and `/health` need a token too | `--disable-authz=health,ping` |
| A `.venv/` appears in the repo | The processing engine creates its venv in `--plugin-dir` (bind-mounted `./plugins`) | `--virtual-env-location=/home/influxdb3/.venv` and a read-only mount |
| Last/distinct value caches return nothing for existing data | They fill only from writes after `create last_cache` / `create distinct_cache` | Write after creating the cache |
| Last value cache query without a key predicate is slow: all 10k hosts 177–426 ms vs 17–194 ms SQL `GROUP BY`; counting 100k devices by a value column 1.2–1.4 s vs 14–228 ms SQL | The cache is organized by key columns | Use the caches for keyed lookups only |
| `SELECT * FROM system.parquet_files` lists every file once per table with the same table id, including other databases and dropped tables the catalog keeps as `home-20261003T061145` | Missing `WHERE table_name = ...` | Always filter by `table_name` (the examples do) |
| A `count(*)` right after the last `no_sync=true` ack saw part of 2M rows; all of them 1.2–5 s later | `no_sync` acks before the rows are queryable | Poll until the count matches |
| `Query would scan 432 Parquet files, exceeding the file limit. InfluxDB 3 Core caps file access ...` (plus an Enterprise upsell paragraph) | Core does not compact; `--query-file-limit` is 432 | Narrow the time range, or raise the limit (worked for 1,009 small files) |
| MinIO answers `507 Insufficient Storage`. `create database` fails with `object store error: ... retries: 10 ... inner: Status { status: 507 ...`; startup exits with code 1; during a bulk load `ERROR influxdb3_wal::object_store: error writing wal file to object store ... 507 Insufficient Storage`, retried, writes stalled up to 128 s then completed with no rows lost | Shared Docker VM disk full | Keep a few GB free |
| No MinIO community images | MinIO stopped publishing them | The examples use Chainguard's `latest` build pinned by digest |

InfluxDB 1.x cluster fork (`chengshiwen/influxdb:1.8.11-c1.2.0`): see
[`v1-oss-cluster/` Known issues](v1-oss-cluster/README.md#known-issues). The fork is a dormant
third-party project, not from InfluxData; its last release, image and commit (`v1.8.11-c1.2.0`)
are from 2024-09-08, on InfluxDB 1.8, which is itself in maintenance.
