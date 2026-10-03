# TimescaleDB

Website: https://www.tigerdata.com/timescaledb

TimescaleDB is a PostgreSQL extension for time series: hypertables partitioned into time
chunks, a columnstore for older chunks, continuous aggregates, and retention and
columnstore policies. It is plain SQL on Postgres, so joins with relational tables work.

- [`single-node/`](single-node) — one PostgreSQL 18 + TimescaleDB 2.30 server (`timescale/timescaledb-ha`) on Docker Compose, with a SQL walkthrough: hypertables and chunks, columnstore compression, continuous aggregates with real-time aggregation, `time_bucket_gapfill`, toolkit hyperfunctions, and retention.
- [`docker-compose-cluster/`](docker-compose-cluster) — three nodes under Patroni (one primary, two streaming replicas), a 3-member etcd cluster and HAProxy with read-write and read-only ports, plus `make failover`, `make switchover` and `make sync`. This is replication for HA and read scaling, not sharding: TimescaleDB multi-node was removed in 2.14.
- [`fleet-telemetry/`](fleet-telemetry) — a Rust client (`tokio-postgres`, binary `COPY`) that simulates 1000 electric delivery vehicles (14.4M readings). It times the ingest into the rowstore and straight into the columnstore, the compression ratio, and fleet dashboard queries joined with relational metadata, on the rowstore, the columnstore and a continuous aggregate.
- [`multinode-2.13/`](multinode-2.13) — **legacy, end-of-life**: a real sharded TimescaleDB on the last release that had multi-node (`timescale/timescaledb:2.13.1-pg15`). It has an access node and 3 data nodes, a distributed hypertable with `replication_factor => 2`, chunk placement, aggregate pushdown, and a `make failover` that stops a data node and re-replicates the chunks it missed by hand. For learning only.

TimescaleDB's multi-node mode (distributed hypertables) was deprecated in 2.13 and removed in
[2.14.0](https://github.com/timescale/timescaledb/releases/tag/2.14.0) (2024-02-08; see
[MultiNodeDeprecation.md](https://github.com/timescale/timescaledb/blob/main/docs/MultiNodeDeprecation.md)).
A self-hosted TimescaleDB cluster is now Postgres streaming replication: one primary and
replicas that each hold all the data, for HA and read scaling, not sharding. That is what
`docker-compose-cluster/` runs.

`multinode-2.13/` runs that last release, which is unpatched and stuck on PostgreSQL 13-15. On
a supported TimescaleDB, a self-hosted cluster is Postgres streaming replication: one primary
and replicas that each hold all the data, for HA and read scaling, not sharding.

## Benchmark

`timescaledb-parallel-copy` ingest of 10.08M sensor readings, then dashboard queries, on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-10-02). Writing straight into the columnstore reaches 2.98M rows/s with 8 workers, against 524k rows/s into the rowstore. The columnstore is 7.7x smaller (151 MiB vs 1,164 MiB), and full-scan aggregates run 3-5x faster on it (daily max per device: 1,302 -> 399 ms). A continuous aggregate answers the same query in 28 ms. Full tables and method: [`single-node/README.md`](single-node/README.md#benchmark).

Patroni cluster, 2 CPUs / 4 GiB per node (same hardware and date): a 2.88M-row load writes 566 MiB of WAL into the rowstore against 41 MiB straight into the columnstore (2.47M vs 580k rows/s), so the replicas also replay 14x less. Synchronous replication costs nothing measurable for 5,000-row `COPY` batches. Two replicas serve 16.1k read queries/s, against 8.0k/s on the primary alone. Stopping the primary interrupted writes for ~18 s and lost no acknowledged insert. Details: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).

Fleet telemetry example (Rust client, 4 binary `COPY` connections, server capped at 4 CPUs / 6 GB, same hardware, 2026-10-03): 14.4M readings from 1000 vehicles load at 5.8M rows/s straight into the columnstore, against 1.1M rows/s into the rowstore. The columnstore takes 213 MiB against 2,020 MiB (9.5x smaller). Dashboard tiles that join telemetry with `vehicles`/`fleets` take 27-41 ms on an hourly continuous aggregate, 0.63-0.66 s on the columnstore and 0.76-1.3 s on the rowstore. Details: [`fleet-telemetry/README.md`](fleet-telemetry/README.md#results).

Multi-node 2.13 (end-of-life), 2.88M rows loaded with `timescaledb-parallel-copy` through the access node, each of the 4 containers capped at 2 CPUs / 2 GiB (same hardware, 2026-10-03): a distributed hypertable on 3 data nodes runs full-table aggregates about 2x faster than a local hypertable on one node (max per device 71 vs 124 ms). Ingest is about the same with replication factor 1 (1.20M vs 1.16M rows/s) and half as fast with replication factor 2 (582k rows/s, two-phase commit to two nodes). After stopping a data node, every query failed until `alter_data_node(..., available => false)`, and repairing the 6 under-replicated chunks took a manual `copy_chunk` of ~5 s per chunk. Details: [`multinode-2.13/README.md`](multinode-2.13/README.md#benchmark).

## Known issues

TimescaleDB is actively maintained: 2.30.2 shipped on 2026-09-29, and minor releases come
roughly monthly. The company behind it renamed itself Tiger Data in 2025, so docs and links
move between timescale.com and tigerdata.com. The API is still changing. The "compression"
functions became the "columnstore" functions in 2.18, and older blog posts and answers use
names and call styles that no longer work. Seen while building these examples (2026-10-02,
TimescaleDB 2.30.2, `timescale/timescaledb-ha:pg18.6-ts2.30.2`):

- The columnstore functions are procedures. `SELECT convert_to_columnstore(c) FROM show_chunks(...)`
  fails with `ERROR: convert_to_columnstore(regclass, if_not_columnstore => boolean) is a procedure`
  (`HINT: To call a procedure, use CALL.`). The same goes for `add_columnstore_policy` and
  `remove_columnstore_policy`. The examples `CALL` them, inside a `DO` loop over `show_chunks()`
  where there are several chunks.
- `CREATE TABLE ... WITH (tsdb.hypertable)` adds a columnstore policy on its own, with
  `compress_after` set to the chunk interval. A later `add_columnstore_policy(..., if_not_exists => true)`
  only prints `WARNING: columnstore policy already exists for hypertable "conditions"` and keeps
  the old one, so the walkthrough removes it and adds its own.
- `timescaledb-parallel-copy` v0.11 writes straight into the columnstore by default (direct
  compress). For chunks written that way, `hypertable_columnstore_stats` returns NULL
  before/after sizes, so there is no ratio to read. The benchmark uses `--disable-direct-compress`
  to get a rowstore table, measures it, and then converts it.
- `locf(round(avg(x), 2))` works, but `round(locf(avg(x)), 2)` fails with
  `ERROR: locf must be toplevel function call`. The same applies to `interpolate()`.
- Multi-node (distributed hypertables) was deprecated in 2.13 and removed in 2.14 (January 2024).
  Scaling out now means Postgres streaming replication for HA and read replicas, not sharding.
- `timescale/timescaledb-ha` is ~650 MB to pull and 3 GB unpacked. The Alpine
  `timescale/timescaledb` image (~500 MB) has no `timescaledb_toolkit`. On a shared, nearly full
  Docker VM, the 10M-row benchmark once failed with
  `ERROR: could not extend file "base/5/84390": No space left on device`. Keep ~5 GB free.
- Patroni cluster (Patroni 4.1.5, etcd v3.6.15): after `docker stop` of the primary, Postgres
  shut down within a second, but the leader key stayed in etcd until its 20 s TTL expired
  (`etcdctl get /service/tsdb/leader` still returned the stopped node, and the container exited
  137). The replicas promoted only then, about 17 s later. Running Patroni under tini
  (`init: true`) changed nothing, so the example keeps the image's entrypoint and documents the
  TTL wait. `make switchover` does not have this problem (~6 s).
- The leader key's TTL, not Postgres, sets the failover time: ~17 s with `ttl: 20`. Shorter TTLs
  fail over faster but risk false failovers on a loaded laptop.
- There is no Kubernetes variant (CloudNativePG + a TimescaleDB image). With the shared Docker
  VM's disk nearly full (the 3 GB timescaledb-ha image plus a kind node), it was not cheap to
  add.

- Multi-node 2.13 (`timescale/timescaledb:2.13.1-pg15`, end-of-life): every multi-node call warns
  `WARNING: adding data node is deprecated` / `DETAIL: Multi-node is deprecated and will be removed in future releases.`
  A stopped data node makes every query on a distributed hypertable fail
  (`ERROR: could not connect to "dn2"`), even with `replication_factor => 2`, until
  `alter_data_node('dn2', available => false)`. Writes made meanwhile leave chunks
  under-replicated (`WARNING: insufficient number of data nodes`), and nothing repairs them
  automatically.
- `timescaledb_experimental.copy_chunk` fails with `ERROR: [dn1]: logical decoding requires wal_level >= logical`
  unless the data nodes run with `wal_level=logical`. It then fails with
  `ERROR: [dn2]: relation "_dist_hyper_1_3_chunk" already exists` if the returning node still has a
  stale copy. The example drops the stale copy with `distributed_exec` first. Each copy took ~5 s,
  even for small chunks.
