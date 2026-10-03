# TimescaleDB 2.13 — multi-node (distributed hypertables), end-of-life

> **Deprecated and removed. For learning only.** Multi-node was deprecated in TimescaleDB 2.13
> and removed in [2.14.0](https://github.com/timescale/timescaledb/releases/tag/2.14.0)
> (2024-02-08; see
> [MultiNodeDeprecation.md](https://github.com/timescale/timescaledb/blob/main/docs/MultiNodeDeprecation.md)).
> This example pins `timescale/timescaledb:2.13.1-pg15` (January 2024), the last image with it.
> It receives no fixes or security patches, runs on PostgreSQL 15.5, and has no upgrade path to
> current TimescaleDB except migrating the data out. Every multi-node call prints
> `WARNING: ... is deprecated` / `DETAIL: Multi-node is deprecated and will be removed in future releases.`

An access node (`an1`, database `tsdb`) and three data nodes (`dn1`..`dn3`). A distributed
hypertable is partitioned by time and by `device_id`, with chunks spread over the data nodes and
each chunk stored on 2 of them (`replication_factor => 2`). The access node plans each query and
pushes filters and aggregates down to the data nodes.

```bash
make up        # an1 + dn1..3 (localhost:5470 = access node, user postgres, password tsdb-demo)
make test      # sql/01: add_data_node x3; sql/02: distributed hypertable, 1.3M rows, chunk placement, pushdown EXPLAIN
make failover  # stop dn2, read/write around it, start it, re-replicate the chunks it missed
make benchmark # local vs distributed (RF 1, RF 2) hypertable: parallel COPY ingest + queries
make status    # data nodes, hypertables with distribution and replication factor
make cli       # psql on the access node
make down      # remove containers and volumes
```

- Image `timescale/timescaledb:2.13.1-pg15`: the Alpine image with the Timescale License (TSL)
  features, not `-oss`, which lacks multi-node. Multi-arch, native on arm64. Multi-node in 2.13 is
  available for PostgreSQL 13, 14 and 15 only (MultiNodeDeprecation.md).
- License: multi-node is a TSL ("community") feature. The TSL allows self-hosted use, including
  this kind of learning setup. It forbids offering TimescaleDB as a hosted database service.
- Data nodes run with `max_prepared_transactions=150` (the access node commits multi-node writes
  with two-phase commit) and `wal_level=logical` (`copy_chunk` uses logical replication between
  data nodes). The access node also sets `enable_partitionwise_aggregate=on`.
- Data nodes trust connections from inside the compose network
  (`POSTGRES_HOST_AUTH_METHOD=trust`), so the access node needs no password file.
  `add_data_node` bootstraps database `tsdb` and the extension on each data node.

What `make test` shows (from a run on 2026-10-03):

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

The `INSERT ... SELECT generate_series` of 1.3M rows through the access node took 11.9 s
(~109k rows/s): every row is routed to two data nodes and committed with two-phase commit.

### Failover

`make failover` ([`failover.sh`](failover.sh)) stops `dn2` and shows what happens. Nothing is
automatic:

1. With `dn2` down, **every** query on the hypertable fails, although each chunk has a live
   replica: `ERROR: could not connect to "dn2"`.
2. `alter_data_node('dn2', available => false)` tells the access node to stop using it. Reads
   then go to the remaining replicas (count still 1,296,000).
3. Writes work, but with
   `WARNING: insufficient number of data nodes ... not enough data nodes to replicate chunks according to the configured replication factor`.
   Existing chunks that had a replica on `dn2` drop it from their replica list, and new chunks get
   one replica.
4. After `docker start` and `alter_data_node('dn2', available => true)`,
   `timescaledb_experimental.chunk_replication_status` lists the 6 under-replicated chunks.
   `dn2` still holds stale copies of some of them.
5. Repair, one chunk at a time: drop the stale copy on the target (`distributed_exec('DROP TABLE ...')`),
   then `CALL timescaledb_experimental.copy_chunk(chunk, source, target)`. Each call took ~5.1 s
   even for small chunks. Afterwards 0 of 15 chunks were under-replicated and the row count
   was intact.

## Benchmark

`make benchmark` ([`bench/run.sh`](bench/run.sh)) loads 1000 devices x 2 days x 1/minute =
2.88M rows from a CSV with `timescaledb-parallel-copy` (4 workers, batches of 5,000) through the
access node into three layouts. It then runs [`bench/queries.sql`](bench/queries.sql) 5 times on
each: max per device, hourly average over everything, one device's day in 5-minute buckets, and
a filtered count. [`bench/limits.sh`](bench/limits.sh) caps each of the 4 containers at
2 CPUs / 2 GiB (`BENCH_CPUS=8`, `BENCH_MEM=8g`). The client has 2 CPUs. Results go to
`results/` (gitignored). `make benchmark SMOKE=1` runs 1 day.

2026-10-03, Docker Desktop 29.5.3, Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64),
TimescaleDB 2.13.1 on PostgreSQL 15.5, timescaledb-parallel-copy 0.4.1-dev:

| layout | rows/s | size | max-per-device | hourly-avg-all | one-device-1day | count-threshold |
|---|---:|---:|---:|---:|---:|---:|
| local hypertable on the access node (2 CPUs) | 1,162,228 | 166 MiB | 123.7 ms | 126.4 ms | 24.8 ms | 41.4 ms |
| distributed, 3 data nodes, RF 1 | 1,200,000 | 317 MiB | 70.8 ms | 63.2 ms | 3.4 ms | 21.1 ms |
| distributed, 3 data nodes, RF 2 | 581,936 | 633 MiB | 64.9 ms | 63.7 ms | 3.6 ms | 21.0 ms |

- **Scans scale out.** With 3 data nodes doing the work, the full-table aggregates run about 2x
  faster than on one node of the same size. The access node merges per-node results and stays
  the bottleneck, so 3x the CPUs did not give 3x.
- **Ingest does not.** With RF 1 the access node routes rows and is as fast as a local hypertable
  (~1.2M rows/s). RF 2 writes every row twice under two-phase commit and halves the rate.
- **Sizes and the point query** differ partly because of indexes. A distributed hypertable with a
  space dimension gets a `(device_id, time)` index on the data nodes, while the local
  hypertable here has only the default `time` index. That is why `one-device-1day` is 3.4 ms
  against 24.8 ms, and why RF 1 takes 317 MiB against 166 MiB. RF 2 doubles the storage.
- None of this is available on a supported TimescaleDB. For scale-out today, the options are
  Timescale's managed service (Tiger Cloud), read replicas, or a different system (Citus for
  sharded Postgres).
