# TimescaleDB 2.13 — multi-node (distributed hypertables), end-of-life

> **Deprecated and removed. For learning only.** Multi-node was deprecated in TimescaleDB 2.13 and removed in [2.14.0](https://github.com/timescale/timescaledb/releases/tag/2.14.0) (2024-02-08; see [MultiNodeDeprecation.md](https://github.com/timescale/timescaledb/blob/main/docs/MultiNodeDeprecation.md)). This example pins `timescale/timescaledb:2.13.1-pg15` (January 2024), the last image with it: no fixes or security patches, PostgreSQL 15.5, no upgrade path to current TimescaleDB except migrating the data out.

An access node (`an1`, database `tsdb`) and three data nodes (`dn1`..`dn3`). A distributed hypertable is partitioned by time and `device_id`; chunks are spread over the data nodes, each stored on 2 of them (`replication_factor => 2`). The access node plans each query and pushes filters and aggregates down to the data nodes.

## Quick start

```bash
make up        # an1 + dn1..3 (localhost:5470 = access node, user postgres, password tsdb-demo)
make test      # sql/01: add_data_node x3; sql/02: distributed hypertable, 1.3M rows, chunk placement, pushdown EXPLAIN
make failover  # stop dn2, read/write around it, start it, re-replicate the chunks it missed
make benchmark # local vs distributed (RF 1, RF 2) hypertable: parallel COPY ingest + queries
make status    # data nodes, hypertables with distribution and replication factor
make cli       # psql on the access node
make down      # remove containers and volumes
```

## Configuration

| item | detail |
|---|---|
| image | `timescale/timescaledb:2.13.1-pg15`: Alpine, with Timescale License (TSL) features (not `-oss`, which lacks multi-node). Multi-arch, native on arm64. Multi-node in 2.13 supports PostgreSQL 13, 14 and 15 only (MultiNodeDeprecation.md). |
| license | multi-node is a TSL ("community") feature: self-hosted use, including this setup, is allowed; offering TimescaleDB as a hosted database service is not |
| data nodes | `max_prepared_transactions=150` (access node commits multi-node writes with two-phase commit), `wal_level=logical` (`copy_chunk` uses logical replication between data nodes), `POSTGRES_HOST_AUTH_METHOD=trust` inside the compose network, so the access node needs no password file |
| access node | `enable_partitionwise_aggregate=on`; `add_data_node` bootstraps database `tsdb` and the extension on each data node |

## What `make test` shows

Run on 2026-10-03:

```
 chunk_name            | day        | data_nodes          -- 3 days x 3 space partitions = 9 chunks
 _dist_hyper_1_1_chunk | 2026-09-30 | {dn1,dn2}
 _dist_hyper_1_4_chunk | 2026-09-30 | {dn2,dn3}
 _dist_hyper_1_7_chunk | 2026-09-30 | {dn1,dn3}
 ...
 node_name | size     -- 1.3M rows, each stored twice
 dn1       | 98 MB
 dn2       | 89 MB
 dn3       | 91 MB

 Custom Scan (AsyncAppend)                       -- GROUP BY device_id: full aggregate on each node
   ->  Custom Scan (DataNodeScan)
         Relations: Aggregate on (public.conditions)
         Data node: dn1
         Chunks: _dist_hyper_1_2_chunk, _dist_hyper_1_3_chunk
         Remote SQL: SELECT device_id, max(temperature) FROM public.conditions WHERE _timescaledb_functions.chunks_in(...) AND (("time" > ...)) GROUP BY 1
   ...
```

The `INSERT ... SELECT generate_series` of 1.3M rows through the access node took 11.9 s (~109k rows/s): every row goes to two data nodes under two-phase commit.

## Failover

`make failover` ([`failover.sh`](failover.sh)) stops `dn2`. Nothing is automatic:

| step | result |
|---|---|
| 1. `dn2` down | **every** query on the hypertable fails, although each chunk has a live replica: `ERROR: could not connect to "dn2"` |
| 2. `alter_data_node('dn2', available => false)` | access node stops using it; reads go to the remaining replicas (count still 1,296,000) |
| 3. writes | succeed with `WARNING: insufficient number of data nodes ... not enough data nodes to replicate chunks according to the configured replication factor`. Existing chunks with a replica on `dn2` drop it from their replica list; new chunks get one replica |
| 4. `docker start` + `alter_data_node('dn2', available => true)` | `timescaledb_experimental.chunk_replication_status` lists 6 under-replicated chunks; `dn2` still holds stale copies of some |
| 5. repair, one chunk at a time | drop the stale copy on the target (`distributed_exec('DROP TABLE ...')`), then `CALL timescaledb_experimental.copy_chunk(chunk, source, target)`. ~5.1 s per call even for small chunks. Afterwards 0 of 15 chunks under-replicated, row count intact |

## Benchmark

`make benchmark` ([`bench/run.sh`](bench/run.sh)) loads 1000 devices x 2 days x 1/minute = 2.88M rows from a CSV with `timescaledb-parallel-copy` (4 workers, batches of 5,000) through the access node into three layouts, then runs [`bench/queries.sql`](bench/queries.sql) 5 times on each: max per device, hourly average over everything, one device's day in 5-minute buckets, a filtered count. [`bench/limits.sh`](bench/limits.sh) caps each of the 4 containers at 2 CPUs / 2 GiB (`BENCH_CPUS=8`, `BENCH_MEM=8g`); client 2 CPUs. Results go to `results/` (gitignored). `make benchmark SMOKE=1` runs 1 day.

2026-10-03, Docker Desktop 29.5.3, Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64), TimescaleDB 2.13.1 on PostgreSQL 15.5, timescaledb-parallel-copy 0.4.1-dev:

| layout | rows/s | size | max-per-device | hourly-avg-all | one-device-1day | count-threshold |
|---|---:|---:|---:|---:|---:|---:|
| local hypertable on the access node (2 CPUs) | 1,162,228 | 166 MiB | 123.7 ms | 126.4 ms | 24.8 ms | 41.4 ms |
| distributed, 3 data nodes, RF 1 | 1,200,000 | 317 MiB | 70.8 ms | 63.2 ms | 3.4 ms | 21.1 ms |
| distributed, 3 data nodes, RF 2 | 581,936 | 633 MiB | 64.9 ms | 63.7 ms | 3.6 ms | 21.0 ms |

- **Scans scale out:** full-table aggregates ~2x faster on 3 data nodes than on one node of the same size. The access node merges per-node results and stays the bottleneck, so 3x the CPUs did not give 3x.
- **Ingest does not:** with RF 1 the access node routes rows at local-hypertable speed (~1.2M rows/s). RF 2 writes every row twice under two-phase commit and halves the rate.
- **Sizes and the point query** differ partly because of indexes: a distributed hypertable with a space dimension gets a `(device_id, time)` index on the data nodes; the local hypertable here has only the default `time` index. Hence `one-device-1day` 3.4 vs 24.8 ms, and RF 1 317 vs 166 MiB. RF 2 doubles the storage.
- None of this exists on a supported TimescaleDB. Scale-out options today: Timescale's managed service (Tiger Cloud), read replicas, or a different system (Citus for sharded Postgres).

## Known issues

- **Deprecation warnings.** Every multi-node call prints `WARNING: adding data node is deprecated` (or `... is deprecated`) / `DETAIL: Multi-node is deprecated and will be removed in future releases.`
- **One stopped data node breaks all queries.** `ERROR: could not connect to "dn2"` on every query of a distributed hypertable, even with `replication_factor => 2`. Workaround: `alter_data_node('dn2', available => false)`.
- **No automatic re-replication.** Writes while a node is down leave chunks under-replicated (`WARNING: insufficient number of data nodes`). Workaround: manual `copy_chunk` per chunk (step 5 above).
- **`copy_chunk` needs logical WAL.** Fails with `ERROR: [dn1]: logical decoding requires wal_level >= logical` unless data nodes run with `wal_level=logical` (set here).
- **`copy_chunk` onto a stale copy.** Fails with `ERROR: [dn2]: relation "_dist_hyper_1_3_chunk" already exists` if the returning node still has a stale copy. Workaround: drop it with `distributed_exec` first. Each copy took ~5 s, even for small chunks.
