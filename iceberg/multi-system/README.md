# Iceberg shared storage, multiple engines and archive handoffs

DuckDB, ClickHouse and Spark operate on catalog-managed Iceberg v2 tables on
SeaweedFS. This example belongs under Iceberg because it tests interoperability.
The single-server, ClickHouse-only examples are separately indexed under
[`clickhouse/`](../../clickhouse/README.md#iceberg-examples).

## Quick start

Prerequisites: Docker with Compose v2, Make and Python 3.9+. Allow 16 GiB Docker RAM
and 8 GiB free Docker disk including images. The native serving case runs nine
ClickHouse data servers, three Keeper nodes and a separate query coordinator.
Run from this directory.

1. `make docker-up` — render the topology and start SeaweedFS S3 and REST catalog infrastructure.
2. `make up` — build Dockerized clients and the Spark compactor; start the ClickHouse query server.
3. `make test` — run all five architectures serially, including compaction and replica recovery.
4. `make down` — remove ClickHouse application containers, preserving infrastructure state.
5. `make docker-down` — remove this example's containers, network, volumes and built image tags.

`make test ARCH=duck-direct` selects one architecture. The others are
`ch-direct`, `ch-archive`, `duck-archive` and `ch-sharded`. Each test resets its
own Iceberg table and saves JSON under `results/manual/<architecture>/`.
Use `make test ROWS=4000000 CUSTOMERS=20000 BATCH_ROWS=100000` to change counts.
`TARGET_FILE_BYTES=134217728` controls the compaction target. `make compact`
maintains `TABLE=lab.events`; specify the same `RESULTS_DIR` to retain its metrics
beside a particular run. `make benchmark QUERY_RUNS=20` reruns prepared baselines and measures full scans
and customer filters after compaction (2 warmups, 20 timed iterations).
Additional commands: `make probe`, `make status`,
`make cli`, `make cluster-failure-test` (after `cluster-test`).

## Architectures

| Case | Data path | Purpose |
|---|---|---|
| `duck-direct` | DuckDB → Iceberg → ClickHouse | Shared committed data across independent engines |
| `ch-direct` | ClickHouse → Iceberg → DuckDB | Real catalog-visible ClickHouse appends |
| `ch-sharded` | DuckDB → Iceberg → native ClickHouse, 3 shards × 3 replicas | Replicated serving cache, verified generation publication and replica recovery |
| `ch-archive` | ClickHouse hot → verified Iceberg archive → TTL deletes hot copies | Safe hot/cold retention boundary |
| `duck-archive` | Local DuckDB hot → verified Iceberg archive → local transactional deletion | Embedded hot storage with a portable cold archive |

All five use SeaweedFS 4.53 for S3 objects and its persistent REST catalog. No
MinIO is used. Spark 4.0.1 / Iceberg 1.10.1 owns data-file bin packing and manifest
rewrites through that same catalog. It verifies that row counts survive and
that the old snapshot remains readable. Snapshot expiration and orphan deletion
are separate retention operations and do not run here.

Compaction publishes new Iceberg metadata; S3 vacuuming and MergeTree merges do
not replace it. A 128 MiB target is not a minimum file size: this compressible
2-million-row dataset is much smaller.

Native ClickHouse serving data is deliberately materialized into an unpublished
new generation. Distributed inserts use quorum 2 and synchronous delivery. Each
of all nine copies is checked for exact shard placement, unique IDs, values and
totals before publication. Query one replica per shard; replicas must not multiply
logical rows. During the fault test `ch21` is stopped, another batch commits to
Iceberg, and a new generation is built on eight available replicas. Queries keep
using the old complete generation. After the ninth replica catches up, the worker
publishes the new complete generation. Writers are fenced while each refresh
reads its source; this example does not claim an atomic multi-shard append.

Both archive cases seal an old ID/date period. Export → catalog commit → exact
cross-engine verification occurs before destructive hot cleanup. ClickHouse
`TTL ... TO DISK/VOLUME` keeps MergeTree format: it does not export Iceberg.
The ClickHouse archive worker instead authorizes an expiry mutation only after
verification, then uses `MATERIALIZE TTL` to make the demonstration finish.
DuckDB deletes hot rows in a local transaction after archive verification; one
process owns its local `.duckdb` file. Production writers must enforce the seal
against late arrivals. Archive failure/resume gates are also exercised in the
[ClickHouse-only example](../../clickhouse/iceberg/README.md).

The shared ClickHouse worker also has `make unit-test`: negative reconciliation
cases and a simulated lost append acknowledgement that must never be replayed.
These run before the full live matrix.

## Directory structure

```text
multi-system/
├── Makefile                 # five-command lifecycle, matrix, benchmark
├── docker-compose.yml       # SeaweedFS, coordinator and Dockerized clients
├── app/                     # DuckDB load/archive, cross-engine oracle, native generations
├── config/                  # query limits; generated 3-shard/3-replica/Keeper topology
├── scripts/                 # topology renderer and architecture matrix runner
├── PLAN.md                  # unimplemented experiments, explicitly separate from results
└── results/                 # ignored JSON metrics and acknowledgement journals
```

The ClickHouse-only worker and Spark compactor are reused from
`../../clickhouse/iceberg/app/` and `../../clickhouse/iceberg/compactor/`.

## Limitations by architecture

| Case | Useful for | Limitation |
|---|---|---|
| `duck-direct` | Embedded ingestion with independent SQL readers | One designated writer; no multi-writer conflict or schema-evolution coverage. Small files need scheduled compaction. |
| `ch-direct` | SQL-driven lake appends readable by DuckDB | Pinned ClickHouse Iceberg write path is beta; it cannot append to the tested populated DuckDB snapshot because of its missing summary field. |
| `ch-sharded` | Native indexed serving and replica recovery over a lake source | Full-generation refresh duplicates storage and fences source writers; readers remain on a stale generation until all nine copies validate. No automatic incremental refresh. |
| `ch-archive` | Verified hot/cold retention with native hot queries | Application must seal old periods and export/verify before arming TTL. Late arrivals, corrections and automatic scheduling are not implemented. |
| `duck-archive` | Archiving a single-owner embedded database | Local transaction and catalog commit are separate; resume must reconcile state. Independent processes cannot directly write one native DuckDB file. |

All cases share one SeaweedFS mini instance and host/disk. Nine serving copies do
not make the source objects highly available. Data is synthetic, compressible and
fits memory; warm-query measurements do not establish cold-object-store latency,
WAN behavior or production capacity. Old snapshots/files remain until a separate,
reader-aware retention policy expires them. Catalog contention, compactor crashes
and storage failure remain untested experiments in `PLAN.md`.

## Verified baseline numbers

Live run on 2026-11-14, native aarch64 Docker VM with 11 CPUs / 24.42 GiB RAM.
DuckDB 1.5.5 (Go SDK v2.10505.0); ClickHouse 26.10.2.103. Limits: query coordinator
2 CPUs/1.5 GiB, each native data node 1 CPU/1.25 GiB, Keeper 256 MiB each, DuckDB
2 CPUs/2 GiB (`1GB` engine memory), Spark local[2]/2 GiB (1 GiB driver), SeaweedFS
1 GiB. Native query memory is 768 MiB. These are correctness checks on one host,
not production throughput benchmarks.

The initial shared-storage run verified 2,000,000 distinct rows, 10,000 customers,
20 × 100,000-row batches and 10,001,000,000 total cents in both engines.

| Compaction metric | Before | After |
|---|---:|---:|
| Data files | 20 | 1 |
| Data bytes | 17,297,012 | 6,517,399 |
| Rewritten manifests | 21 | 1 |
| Current rows | 2,000,000 | 2,000,000 |
| Retained snapshot rows | 2,000,000 | 2,000,000 |

Data-file and manifest rewrite procedures took 2.842 seconds together. Both
engines found zero missing, duplicate or invalid IDs afterward. This is one
measured rewrite; it excludes Spark startup and is not a general performance
claim. The first native serving generation validated all 9 replicas with
2,000,000 logical rows in 1.706 seconds (generation load, verification and
publication, excluding cluster startup). Full matrix results are saved per case.

The full five-case run also measured:

| Case | Logical rows checked | Data files before → after | Additional outcome |
|---|---:|---:|---|
| `duck-direct` | 2,000,000 | 20 → 1 | Both engines passed the complete oracle |
| `ch-direct` | 2,000,000 | 20 → 1 | DuckDB read ClickHouse-written data after compaction |
| `ch-archive` | 2,000,000 total; 1,000,000 cold | 10 → 1 | 1,000,000 hot rows retained |
| `duck-archive` | 2,000,000 total; 1,000,000 cold | 10 → 1 | Local deletion preserved exact hot/cold coverage |
| `ch-sharded` | 2,000,000 → 2,100,000 | Compaction precedes serving refresh | All 9 replicas checked; recovery and publication took 0.832 s |

The replica-outage report recorded 2,100,000 source rows while the published
cache stayed at 2,000,000; eight replicas held the unpublished refresh. Missing
and duplicate IDs were zero after all nine recovered. Recovery timing excludes
the service restart/health wait and includes replica synchronization, oracle
checks and publication.

## Exact shard placement

The fresh correctness run checked each replica independently, not just the
Distributed table total. Every customer routes by
`cityHash64(Int32(customer_id)) % 3`; all events for that customer stay together.
Counts are deliberately not assumed to be an equal third.

| Shard | Initial rows on each of its 3 replicas | Rows on each replica after recovery |
|---|---:|---:|
| 1 (`ch11`, `ch12`, `ch13`) | 671,400 | 704,970 |
| 2 (`ch21`, `ch22`, `ch23`) | 655,400 | 688,170 |
| 3 (`ch31`, `ch32`, `ch33`) | 673,200 | 706,860 |
| Logical total (one copy per shard) | 2,000,000 | 2,100,000 |

Each of the nine reports matched its expected count and cents total and found
zero invalid or incorrectly placed rows. The published view had exactly that
many distinct IDs; replicas did not multiply the logical result. During `ch21`'s
outage the eight-copy refresh remained unpublished. After restart, all nine
copies passed; synchronization/check/publication took 0.832 s (excludes restart).
Raw evidence: `results/reliability/ch-sharded/ownership-*.json`,
`replica-outage.json` and `replica-recovery.json`. New-table startup now waits for
`is_readonly=0` and `is_session_expired=0` before inserting or synchronizing;
a SQL ping alone is insufficient for replicated-table readiness.

## Benchmark

2026-11-14, Apple M4 Pro, Docker VM aarch64 (11 CPUs / 24.42 GiB), caps listed above. Other deployments were stopped. One serial run per architecture, 2 warmups plus 20 timed iterations per query. Full query: `count, sum(amount_cents)`; filtered query: the same aggregate for customer 42. Every iteration checks its expected result. Warm OS/object caches; ClickHouse result cache disabled. p99 is the maximum of 20 samples, not a production tail estimate. Native TCP and embedded DuckDB paths differ, so these are deployment observations, not engine rankings.

| Architecture | Reader | Logical rows | Full aggregate p50 (ms) | p99 (ms) | Customer filter p50 (ms) | p99 (ms) |
|---|---|---:|---:|---:|---:|---:|
| `duck-direct` | clickhouse | 2,000,000 | 18.192 | 27.501 | 23.742 | 25.413 |
| `duck-direct` | duckdb | 2,000,000 | 12.507 | 15.067 | 19.048 | 20.039 |
| `ch-direct` | clickhouse | 2,000,000 | 15.665 | 22.055 | 20.780 | 23.987 |
| `ch-direct` | duckdb | 2,000,000 | 13.281 | 15.048 | 19.183 | 20.464 |
| `ch-archive` | clickhouse | 1,000,000 | 7.436 | 8.624 | 5.700 | 6.628 |
| `ch-archive` | duckdb | 1,000,000 | 5.198 | 7.032 | 6.271 | 6.872 |
| `duck-archive` | clickhouse | 1,000,000 | 9.217 | 10.465 | 11.863 | 13.729 |
| `duck-archive` | duckdb | 1,000,000 | 7.010 | 7.641 | 10.240 | 11.260 |
| `ch-sharded` | clickhouse | 2,000,000 | 4.938 | 12.986 | 3.340 | 3.984 |

Fresh preparation and maintenance phases (server-generated SQL, not application-payload throughput). These include first-append startup effects; the first DuckDB load took 9.788 s, while the later warmed load took 0.432 s. They are not steady-state ingestion rates:

| Architecture | Preparation | Elapsed (s) | Row rate (rows/s) | Iceberg files before → after | Rewrite procedures (s) |
|---|---|---:|---:|---:|---:|
| `duck-direct` | 2M DuckDB lake load | 9.788 | 204,338 | 20 → 1 | 2.794 |
| `ch-direct` | 2M ClickHouse lake load | 0.529 | 3,782,575 | 20 → 1 | 2.704 |
| `ch-archive` | 2M native hot load | 0.152 | 13,160,907 | 10 → 1 | 2.052 |
| `duck-archive` | 1M export, verify and local delete | 0.657 | — | 10 → 1 | 2.053 |
| `ch-sharded` | 2M DuckDB lake load | 0.432 | 4,624,769 | 20 → 1 | 2.676 |

The performance run's native generation load, nine-copy verification and publication
took 1.718 s, excluding cluster startup.

Archive reader queries cover 1 million cold rows; the correctness suite separately checks the remaining 1 million hot rows and complete hot/cold coverage. Spark procedure time excludes JVM startup. Synthetic data compresses to only a few MiB per table and does not saturate storage. Raw samples: `results/performance/<architecture>/benchmark.json`; bulk phases and compaction are in adjacent JSON/journals.

## Compatibility and failure limits

This ClickHouse release requires explicit beta settings for Iceberg catalog and
writes. A direct append to a DuckDB-created snapshot failed with:
`Cannot derive Iceberg snapshot total 'total-files-size': the parent snapshot's summary omits it`.
The tested reverse path uses an empty catalog table before ClickHouse's data
appends. This is not proof that the pinned engines can alternate writes to the
same populated table. The cross-engine smoke probe exercises separate writer
ownership in each direction.

The catalog's committed metadata location is authoritative. Do not select the
highest-looking metadata JSON filename from S3. Independent readers are freshly
attached/resolved after commits. Appends with SQL/network errors are reported as
unresolved, never blindly replayed: Iceberg has no primary-key uniqueness
constraint. A completed batch journal is flushed outside the object store.
Multi-writer contention, read-during-commit, compactor interruption and storage
failure cases remain tracked in [PLAN.md](PLAN.md); passing the normal matrix
does not claim those additional tests passed.

S3 and catalog are on localhost ports 18333 and 18181 respectively; override
`S3_PORT` and `CATALOG_PORT` if necessary. Demo credentials are public `demo/demo`
and ClickHouse's password is `local-reader`. Services communicate over a private
Compose network. The nine copies, Keeper and SeaweedFS all share one host/disk:
this is not a multi-host durability or disk-failure demonstration.

## Sources and design rules

Official: [DuckDB Iceberg writes](https://duckdb.org/docs/current/core_extensions/iceberg/writing),
[SeaweedFS catalog and endpoints](https://github.com/seaweedfs/seaweedfs/wiki/SeaweedFS-Iceberg-Catalog),
[Iceberg Spark procedures](https://iceberg.apache.org/docs/1.10.0/spark-procedures/),
[ClickHouse TTL](https://clickhouse.com/docs/guides/developer/ttl).
Pinned source inspected: SeaweedFS `handlers_commit.go` (catalog CAS), ClickHouse
`RestCatalog.cpp`, `DatabaseDataLake.cpp`, `IcebergWrites.cpp`,
`StorageSystemReplicas.cpp` (Keeper-session readiness), and Iceberg's
`RewriteDataFilesProcedure.java`. Generations, writer fencing and archive
verification gates are derived application designs.

Applied rules: `decision-ingestion-strategy`, `schema-pk-plan-before-creation`,
`schema-pk-prioritize-filters`, `schema-partition-lifecycle`,
`schema-types-native-types`, `insert-batch-size`, `insert-mutation-avoid-update`,
`insert-optimize-avoid-final`, `agent-discovery-schema`, `agent-query-safety`.
MergeTree lifecycle and sort-key rules apply to the native cache/hot tier;
Iceberg file layout and compaction have their own metadata and maintenance.
