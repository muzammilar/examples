# StarRocks — clustered mutable orders vs ClickHouse

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

| Component | Version/topology | Per-container limits |
|---|---|---|
| StarRocks FE | 4.1.7; 3 metadata/query frontends | 0.5 CPU / 1.25 GiB; 640 MiB Java heap |
| StarRocks BE | 4.1.7; 3 workers, 3 hash buckets × 3 copies | 1 CPU / 2 GiB; explicit 1024 MiB native budget |
| ClickHouse | 26.10.2.103; 3 customer shards × 3 replicas, 9 servers | 0.4 CPU / 1 GiB; 800,000,000-byte server limit |
| Keeper | 26.10.2.103; 3 metadata voters | 0.3 CPU / 256 MiB |
| Workload / live reader | Go; independent processes | 2 CPUs / 2 GiB; reader 0.5 CPU / 256 MiB |

Each complete database system has **4.5 CPUs / 9.75 GiB** of container limits.
StarRocks stores three bucket copies across three BEs; ClickHouse spreads three
shards across nine data servers. These are different role layouts with the same
aggregate budget, not nine StarRocks data nodes.

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
| initial | 85,913.5 | 51,310.3 | 1.67× |
| updates | 66,068.5 | 49,796.0 | 1.33× |
| stale-replay | 65,821.6 | 51,639.3 | 1.27× |

| Query | StarRocks p50 / p99 | ClickHouse p50 / p99 | p99 SR / CH |
|---|---:|---:|---:|
| customer-current-state | 12.215 ms / 84.969 ms | 6.556 ms / 30.075 ms | 2.83× |
| all-current-state | 79.660 ms / 101.165 ms | 51.297 ms / 85.607 ms | 1.18× |
| region-join | 89.362 ms / 184.561 ms | 149.221 ms / 285.788 ms | 0.65× |

All **1,000,000 current orders per system** matched; zero missing, wrong or duplicate
logical rows. The dimension join matched all 10 independently calculated sums.
ClickHouse replicas were read individually, including customer-to-shard routing.
StarRocks bucket contents were read and native per-copy checksums compared; those
checksums are XOR32, not cryptographic evidence of identical physical files.

## Live recovery experiments

`make test-production` runs the following serially, with separate targets. Each
keeps an independent writer and reader live through the fault and native repair.
The default recovery writer sends `FAULT_BATCH_ROWS=1000` rows per request and
waits `FAULT_PAUSE_MS=1000` between batches. Override them in Make; this paced
probe is not a maximum ingestion-rate benchmark. Reader probes sample immutable
baseline orders; they do not measure fresh-write visibility lag. Fault writes are
append-only recovery events, not concurrent updates to the baseline order rows.

| Target | Injected failure | ACK rows | Native repair incl. 30 s hold | Ambiguous requests |
|---|---|---:|---:|---:|
| `make failover` | sr-be-0; SIGKILL, retained disk | 46,000 | 62.252 s | 1 |
| `make disk-failure` | sr-be-0; empty owned volume | 39,000 | 52.523 s | 2 |
| `make failover FAULT_SERVICE=sr-be-1` | sr-be-1; SIGKILL, retained disk | 49,000 | 67.964 s | 2 |
| `make chaos-fe-leader` | sr-fe-0; SIGKILL, retained disk | 51,000 | 54.663 s | 1 |
| `make comparison-failover` | ch00; SIGKILL, retained disk | 45,000 | 47.442 s | 1 |
| `make comparison-disk-failure` | ch00; empty owned volume | 54,000 | 57.225 s | 1 |
| `make comparison-failover FAULT_SERVICE=ch01` | ch01; SIGKILL, retained disk | 47,000 | 48.353 s | 0 |
| `make chaos-keeper-leader` | keeper2; SIGKILL, retained disk | 42,000 | 47.770 s | 0 |

Historical reconciliation: **185,000 StarRocks ACK rows** and
**188,000 ClickHouse ACK rows**, with zero missing,
wrong or duplicate rows. Zero partially committed StarRocks Stream Load batches.
ClickHouse Distributed batches span shards and have no cross-shard atomicity
promise; ambiguous partial outcomes are counted separately.

Raw `*-observations.json` files split completion-time measurements into before,
outage, native recovery and after phases. They retain request counts, successful
and unsuccessful p50/p99, read errors/missing/stale values and ACK throughput.
A frozen native version target and actual BE checksums gate recovery; metadata
readiness alone is insufficient. Cleanup and a subsequent fresh lifecycle passed.

## Observed failures and recovery

- Fresh BE HTTP health preceded native FE registration. Waiting for native
  membership before schema creation fixes `Current alive backend is []`.
- An empty BE advertised healthy metadata while a tablet still did not exist.
  The full reader failed. Physical per-replica checksums now gate repair; a later
  failure of the other BE verifies the repaired cluster remains usable.
- Retained FEs must start together: making peers depend on FE0 SQL health
  deadlocks a cold quorum. Startup uses `service_started` and native quorum waits.
- Earlier 768 MiB ClickHouse replicas exhausted memory during full readback;
  a later small-batch-history fixture also rejected a full sum at the 800 MB
  native limit. Failed runs are preserved. The final fresh run uses the stated
  1 GiB replica cap, bounded readback, two query threads, bounded optional log
  buffers and a slower, larger-batch recovery probe. This does not qualify a soak.
- `INNER ANY JOIN` hid duplicate left customer keys in the initial comparator;
  the independent aggregate oracle rejected it. The verified query is LEFT ANY
  with a unique, fully checked dimension.
- One earlier benchmark returned CLI success without an artifact. Its query
  timings are excluded. The reported rerun requires container exit code 0,
  `OOMKilled=false` and a fresh validated JSON result, saved in `benchmark-exit.json`.

## Limits and unrun cases

Single-host process loss and one empty data volume were tested. This does not
qualify correlated host failure, arbitrary I/O corruption, power loss, multi-site
recovery or production security. Compose scaling, network partitions, backup/
restore, version upgrades and sustained resource/soak tests remain unrun here;
operator-specific experiments live in the separate Kubernetes example. Timings
include polling, orchestration and the deliberate 30 s hold; they are not pure
leader-election times. Journals from earlier fixtures remain in ignored archives.

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

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
