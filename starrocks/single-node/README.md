# StarRocks — single-copy mutable orders

Separate FE and BE roles, versioned order updates, stale replay and a regional
dimension join, compared with ClickHouse on the same deterministic data.

## Quick start

1. `make docker-up` — build local images and wait for native FE/BE membership.
2. `make up` — create schemas and load the million-order fixture.
3. `make test` — check every final value, customer shard and replica.
4. `make down` — remove the workload tables.
5. `make docker-down` — remove only this project’s infrastructure and built images.

Docker Compose, GNU Make, Python 3.10+ and sufficient Docker VM resources are
required. No host Go compiler is needed; no database ports are published.

## Setup

StarRocks 4.1.7 runs one FE (0.5 CPU / 1.25 GiB) and one BE (1 CPU / 2 GiB),
with three hash buckets and one copy. ClickHouse 26.10.2.103 runs one server
(0.4 CPU / 1 GiB) and one Keeper (0.3 CPU / 256 MiB), one shard and one copy.
The workload has 2 CPUs / 2 GiB. These minimal component budgets differ;
this is a functional single-copy baseline, not a normalized capacity ranking.
There is no failover or independent-disk durability claim.

## Source and build

`mutable-orders/` and `docker/` are tracked source symlinks. `prepare.py` turns
them into regular hard-linked files (copies on another filesystem) under the
ignored `.build/` directory before Docker builds. Materialized files are never
committed. The workload is a Compose service, with separate live writer/reader
services for fault tests. No sibling example needs to be run first.

## Workload and correctness

The Go application uses the maintained MySQL driver for StarRocks SQL and standard
HTTP/JSON for Stream Load and ClickHouse. It inserts 1,000,000 orders for 10,000
customers, applies 100,000 version-2 updates and replays 100,000 stale version-1
records. Every final amount/status/version/SHA-256 value is checked. Customer
keys stay immutable. Ten region-join sums have independent expected values;
each dimension must have exactly one correct row per customer.

StarRocks uses Primary Key tables, persistent indexes and Stream Load
`merge_condition=version`; only `Status=Success` is acknowledged. ClickHouse uses
ReplicatedReplacingMergeTree(version), `(customer_id,order_id)` as the sorting
key, `FINAL` for current-state reads, and a customer-sharded Distributed table.
Bulk loads use 10,000-row batches. The ClickHouse dimension is replicated within
each shard and the query uses `LEFT ANY JOIN`, after checking key uniqueness.

Persistence is explicit: StarRocks tablet metadata synchronization is enabled;
FE leader/follower journals use SYNC and majority acknowledgement. ClickHouse
parts and directory changes are fsynced; foreground distributed inserts require
a two-copy quorum in the replicated example, one copy in the single-copy example.
The verifier reads native settings rather than assuming the configuration applied.
A shared Docker VM does not demonstrate host power-loss durability.

## Benchmark

2026-11-15, Apple M4 Pro, native aarch64 Docker; shared 11-CPU / 24.42 GiB VM.
One fresh run. Rates include application generation, serialization and the fsynced
external witness. Queries: 1 warmup + 20 measured repetitions; p99 is the maximum
of those 20 samples, not a production tail-latency estimate.

| Input phase | StarRocks rows/s | ClickHouse rows/s | StarRocks / ClickHouse |
|---|---:|---:|---:|
| initial | 96,291.9 | 104,120.5 | 0.92× |
| updates | 89,911.3 | 111,509.6 | 0.81× |
| stale-replay | 99,333.5 | 102,726.3 | 0.97× |

| Query | StarRocks p50 / p99 | ClickHouse p50 / p99 | p99 SR / CH |
|---|---:|---:|---:|
| customer-current-state | 9.727 ms / 87.282 ms | 3.056 ms / 87.071 ms | 1.00× |
| all-current-state | 46.498 ms / 94.663 ms | 117.090 ms / 188.922 ms | 0.50× |
| region-join | 75.802 ms / 98.659 ms | 217.713 ms / 309.203 ms | 0.32× |

All **1,000,000 current orders per system** matched; zero missing, wrong or duplicate
logical rows. The dimension join matched all 10 independently calculated sums.
ClickHouse replicas were read individually, including customer-to-shard routing.
StarRocks bucket contents were read and native per-copy checksums compared; those
checksums are XOR32, not cryptographic evidence of identical physical files.

## Limits

`make benchmark` is separate from the five-command correctness lifecycle.
The complete lifecycle, million-order checks, query benchmark and owned-resource
cleanup passed. No crash, quorum, disk-replacement, network, backup or upgrade
case was run for this single-copy shape. Use the clustered example for recovery
experiments. Warm repeated queries and fixed synthetic values do not establish
production capacity or performance on a larger working set.

## Sources and design decisions

Official: [StarRocks Primary Key tables](https://docs.starrocks.io/docs/table_design/table_types/primary_key_table/),
[Stream Load](https://docs.starrocks.io/docs/sql-reference/sql-statements/loading_unloading/STREAM_LOAD/),
[replica/quorum behavior](https://docs.starrocks.io/docs/administration/management/resource_management/Replica/),
[BE settings](https://docs.starrocks.io/docs/administration/configuration/BE_parameters/stats_storage/).
Pinned source inspected: [FE durability defaults](https://github.com/StarRocks/starrocks/blob/4.1.7/fe/fe-core/src/main/java/com/starrocks/common/Config.java),
[BE configuration](https://github.com/StarRocks/starrocks/blob/4.1.7/be/src/common/config.h),
[tablet checksums](https://github.com/StarRocks/starrocks/blob/4.1.7/be/src/storage/task/engine_checksum_task.cpp),
[table quorum defaults](https://github.com/StarRocks/starrocks/blob/4.1.7/fe/fe-core/src/main/java/com/starrocks/catalog/TableProperty.java).

Official ClickHouse: [ReplacingMergeTree](https://clickhouse.com/docs/engines/table-engines/mergetree-family/replacingmergetree),
[MergeTree fsync settings](https://clickhouse.com/docs/operations/settings/merge-tree-settings),
[ANY JOIN semantics](https://clickhouse.com/docs/sql-reference/statements/select/join).
Applied repository ClickHouse skill rules: `query-join-use-any`, batched inserts,
immutable ReplacingMergeTree sorting keys, selective key filters and explicit
current-state reads. Lab budgets, three hash buckets and query limits are derived
choices for this bounded corpus, not upstream production sizing recommendations.
