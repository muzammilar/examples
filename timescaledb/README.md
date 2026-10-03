# TimescaleDB

Website: https://www.tigerdata.com/timescaledb · GitHub: https://github.com/timescale/timescaledb

PostgreSQL extension for time series: hypertables partitioned into time chunks, a columnstore for older chunks, continuous aggregates, retention and columnstore policies. Plain SQL on Postgres, so joins with relational tables work.

| folder | what |
|---|---|
| [`single-node/`](single-node) | One PostgreSQL 18 + TimescaleDB 2.30 server (`timescale/timescaledb-ha`) on Docker Compose. SQL walkthrough: hypertables and chunks, columnstore compression, continuous aggregates with real-time aggregation, `time_bucket_gapfill`, toolkit hyperfunctions, retention. |
| [`docker-compose-cluster/`](docker-compose-cluster) | 3 nodes under Patroni (1 primary, 2 streaming replicas), 3-member etcd, HAProxy with read-write and read-only ports. `make failover`, `make switchover`, `make sync`. |
| [`fleet-telemetry/`](fleet-telemetry) | Rust client (`tokio-postgres`, binary `COPY`) simulating 1000 electric delivery vehicles (14.4M readings). Times ingest into the rowstore and straight into the columnstore, the compression ratio, and fleet dashboard queries joined with relational metadata on rowstore, columnstore and a continuous aggregate. |
| [`multinode-2.13/`](multinode-2.13) | **Legacy, end-of-life.** Sharded TimescaleDB on the last release with multi-node (`timescale/timescaledb:2.13.1-pg15`): access node + 3 data nodes, distributed hypertable with `replication_factor => 2`, chunk placement, aggregate pushdown, `make failover` that stops a data node and re-replicates its missed chunks by hand. Learning only. |

## Clustering

Multi-node (distributed hypertables) was deprecated in 2.13 and removed in [2.14.0](https://github.com/timescale/timescaledb/releases/tag/2.14.0) (2024-02-08; see [MultiNodeDeprecation.md](https://github.com/timescale/timescaledb/blob/main/docs/MultiNodeDeprecation.md)). A self-hosted cluster on a supported TimescaleDB is Postgres streaming replication: one primary and replicas that each hold all the data, for HA and read scaling, not sharding. `docker-compose-cluster/` runs that. `multinode-2.13/` runs the last multi-node release, which is unpatched and limited to PostgreSQL 13-15.

## Benchmark

All on an Apple M4 Pro (Docker VM aarch64).

| example | date | caps | result | details |
|---|---|---|---|---|
| single-node, `timescaledb-parallel-copy`, 10.08M sensor readings | 2026-10-02 | 4 CPUs / 6 GB | Ingest: 2.98M rows/s into the columnstore (8 workers) vs 524k rows/s into the rowstore. Columnstore 7.7x smaller (151 vs 1,164 MiB). Full-scan aggregates 3-5x faster on the columnstore (daily max per device: 1,302 -> 399 ms); continuous aggregate 28 ms. | [single-node](single-node/README.md#benchmark) |
| Patroni cluster, 2.88M rows | 2026-10-02 | 2 CPUs / 4 GiB per node | WAL: 566 MiB rowstore vs 41 MiB columnstore (580k vs 2.47M rows/s), so replicas replay 14x less. Sync replication: no measurable cost for 5,000-row `COPY` batches. Reads: 16.1k queries/s on 2 replicas vs 8.0k/s on the primary alone. Stopping the primary: writes down ~18 s, no acknowledged insert lost. | [docker-compose-cluster](docker-compose-cluster/README.md#benchmark) |
| fleet-telemetry, Rust client, 4 binary `COPY` connections, 14.4M readings | 2026-10-03 | server 4 CPUs / 6 GB | Ingest: 5.8M rows/s columnstore vs 1.1M rows/s rowstore. Size: 213 vs 2,020 MiB (9.5x). Dashboard tiles joined with `vehicles`/`fleets`: 27-41 ms on an hourly continuous aggregate, 0.63-0.66 s on the columnstore, 0.76-1.3 s on the rowstore. | [fleet-telemetry](fleet-telemetry/README.md#results) |
| multinode-2.13 (EOL), 2.88M rows via `timescaledb-parallel-copy` through the access node | 2026-10-03 | 2 CPUs / 2 GiB per container (4) | Full-table aggregates ~2x faster on 3 data nodes than a local hypertable (max per device 71 vs 124 ms). Ingest: 1.20M (RF 1) vs 1.16M rows/s (local); 582k rows/s with RF 2 (two-phase commit to two nodes). A stopped data node fails every query until `alter_data_node(..., available => false)`; repairing 6 under-replicated chunks took a manual `copy_chunk` of ~5 s each. | [multinode-2.13](multinode-2.13/README.md#benchmark) |

## Known issues

TimescaleDB is actively maintained: 2.30.2 shipped 2026-09-29, minor releases roughly monthly. The company renamed itself Tiger Data in 2025, so docs and links move between timescale.com and tigerdata.com. The API still changes: the "compression" functions became the "columnstore" functions in 2.18, and older blog posts use names and call styles that no longer work.

Seen while building these examples (2026-10-02, TimescaleDB 2.30.2, `timescale/timescaledb-ha:pg18.6-ts2.30.2`; multi-node on 2.13.1). Details in each example's Known issues:

| issue | example |
|---|---|
| Columnstore functions are procedures: `SELECT convert_to_columnstore(...)` fails, use `CALL` | [single-node](single-node/README.md#known-issues) |
| `WITH (tsdb.hypertable)` adds its own columnstore policy; `add_columnstore_policy(..., if_not_exists => true)` keeps the old one | [single-node](single-node/README.md#known-issues) |
| `timescaledb-parallel-copy` v0.11 direct compress: `hypertable_columnstore_stats` returns NULL sizes | [single-node](single-node/README.md#known-issues) |
| `ERROR: locf must be toplevel function call` (same for `interpolate()`) | [single-node](single-node/README.md#known-issues) |
| `timescale/timescaledb-ha` is ~650 MB to pull / 3 GB unpacked; `No space left on device` on a full Docker VM | [single-node](single-node/README.md#known-issues) |
| Patroni failover waits for the etcd leader key TTL (~17 s with `ttl: 20`) | [docker-compose-cluster](docker-compose-cluster/README.md#known-issues) |
| Multi-node: a stopped data node fails every query; under-replicated chunks are never repaired automatically; `copy_chunk` needs `wal_level=logical` | [multinode-2.13](multinode-2.13/README.md#known-issues) |

- No Kubernetes variant (CloudNativePG + a TimescaleDB image). With the shared Docker VM's disk nearly full (the 3 GB timescaledb-ha image plus a kind node), it was not cheap to add.
