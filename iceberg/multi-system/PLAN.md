# Iceberg multi-system architecture and test plan

Status: all five normal data paths and native replica recovery have passed live tests; see README.md for measured numbers. The broader fault/concurrency matrix below is a roadmap, not a claim that every listed test has run.

## Shared components

- SeaweedFS 4.53 provides S3-compatible object storage and an Iceberg REST catalog, persisted on example-owned volumes. No MinIO.
- Iceberg v2 tables contain Parquet data. The REST catalog's committed metadata location is authoritative; readers must not choose the newest-looking JSON file from an object listing.
- Go clients use DuckDB's Go driver and the ClickHouse Go client. DuckDB processes use independent local databases and attach the shared catalog; no shared writable `.duckdb` file.
- A Spark/Iceberg maintenance job performs data-file and manifest rewrites through the same catalog. It owns compaction, independently of the current writer/reader engine.
- Reuse the already-tested DuckDB 1.5.5 / duckdb-go v2.10505.0 and ClickHouse 26.10.2.103 as compatibility starting points. Pin the Spark/Iceberg runtime and extension artifacts before execution. Verify actual versions in every result.
- Run architectures serially to keep resource use bounded. The nine-node ClickHouse topology is enabled only for its scenarios. All containers still share one host and physical Docker disk.

The workload is append-heavy customer-order analytics, plus sealed-period archival. Native ClickHouse copies are serving caches; Iceberg remains the interoperability/archive layer. This is not an OLTP replacement or an engine-speed ranking.

## Architecture matrix

| Case | Writer / hot store | Shared table | Reader / serving layer | What it demonstrates |
|---|---|---|---|---|
| `duck-direct` | Independent DuckDB writers | Catalog-managed Iceberg on SeaweedFS | ClickHouse queries Iceberg directly | One committed table, independent writer/reader engines, metadata visibility |
| `ch-sharded` | DuckDB commits source snapshots | Iceberg is source of truth | Native ClickHouse serving copy: 3 shards × 3 replicas, Distributed queries, Keeper quorum | Sharded serving, replica recovery, cache freshness and rebuild cost |
| `ch-direct` | ClickHouse writes through the REST catalog | Iceberg on SeaweedFS | DuckDB attaches the catalog and reads | Reverse interoperability, including real catalog-visible writes |
| `ch-archive` | ClickHouse hot MergeTree data | Verified older-period Iceberg archive | DuckDB reads cold data; ClickHouse serves remaining hot data | Verified archive handoff followed by TTL cleanup |
| `duck-archive` | Local DuckDB hot table | Verified older-period Iceberg archive | ClickHouse reads the archive; DuckDB keeps recent data | Embedded hot store, portable archive and safe hot-data deletion |

### Shared reads versus native ClickHouse sharding

A remote Iceberg table does not acquire three storage replicas merely by creating three ClickHouse table definitions. Every reader could otherwise scan the same entire table and double-count results.

`duck-direct` tests independent readers against the same snapshot. Parallel lake scans may use `icebergS3Cluster`, which schedules files across compute nodes; this is different from sharding stored MergeTree data.

`ch-sharded` deliberately materializes a native serving copy. Shard on the customer key, retain three replicas per shard, and query one replica per shard. Use synchronous distributed inserts and quorum acknowledgements. Validate placement and catch-up on all nine copies. Keeper coordinates native replication; it is not an Iceberg catalog.

Publish only a complete serving generation/checkpoint. Partial refreshes remain unpublished; readers keep using the last complete generation. Rebuild from an exact Iceberg snapshot after interruption, and report refresh lag separately from lake visibility. Query result counts must never be multiplied by replicas or the number of shared-storage readers.

## Common workload and correctness oracle

Initial defaults, exposed as Make variables:

| Variable | Proposed default |
|---|---:|
| `ROWS` | 2,000,000 |
| `CUSTOMERS` | 10,000 |
| `BATCH_ROWS` | 100,000 |
| `WRITERS` | 2 |
| `QUERY_RUNS` | 20 |
| `TARGET_FILE_BYTES` | 134,217,728 |

Use deterministic event IDs, customer IDs, two event dates, integer amount cents and stable batch IDs. One schema/bootstrap owner creates namespaces and tables before workers start.

Validate the same logical rows in both engines: exact row count and distinct IDs, min/max ID, per-row deterministic values, amount sum and per-customer aggregates. Preserve a durable external journal of acknowledged batches and committed snapshot IDs. Record missing acknowledged IDs and duplicate IDs separately.

A SQL/network error during commit can be an ambiguous result. Do not blindly replay an append: reconcile the batch against catalog-visible committed data, fence the previous attempt, and preserve an unresolved outcome as a test failure. Iceberg does not provide a primary-key uniqueness constraint. Concurrent append and crash behavior must be established with the pinned writers/catalog, not inferred from object-store durability.

During concurrent reads, query an explicitly selected committed snapshot when comparing engines. A newer independently selected snapshot may contain another complete batch; that is freshness, not corruption. No observed snapshot may contain a partial committed batch. Record catalog/snapshot refresh behavior, including explicit reattachment if the pinned DuckDB reader requires it.

## Compaction owner and lifecycle

Spark/Iceberg owns maintenance in every case:

1. Record current snapshot, data/delete/manifest file counts, file bytes and size distribution.
2. Run Iceberg `rewrite_data_files` with bin packing and a configurable target size.
3. Rewrite manifests where useful and supported.
4. Record the resulting snapshot and metrics.
5. Have DuckDB and ClickHouse validate the same rows before and after; retain and read the previous snapshot.

Many small batch files make the effect observable. File count is measured, not assumed. If the dataset cannot produce target-sized files, report the actual sizes and rewrite outcome instead of claiming a 128 MiB result.

Compaction is an Iceberg metadata commit. It is not a ClickHouse MergeTree merge, TTL storage move, or an object-store vacuum. An interrupted rewrite must leave a readable committed snapshot; unreferenced candidate files are not part of table contents.

Snapshot expiration and orphan-file removal are separate commands, disabled during baseline, concurrent writer and pinned-reader tests. Never delete old files merely because a compaction completed. A later cleanup test needs explicit snapshot retention and an in-flight-write grace period.

## Archive safety

### ClickHouse hot data to Iceberg

Native `TTL ... TO DISK/VOLUME` retains MergeTree parts in another storage tier; it does not produce an interoperable Iceberg table.

The archive controller seals an old date partition, exports it to Iceberg, commits its snapshot, and verifies the exact IDs and totals through the other engine. Only then may it arm the hot partition's expiry. Hot expiry starts disabled/far in the future. A bounded, explicitly measured metadata/expiry mutation is acceptable for this demonstration; it is not the normal per-event write path.

TTL cleanup is asynchronous. Report both when it was armed and when rows disappeared, and require replicas to catch up. Keep hot data if export or verification fails. An interrupted handoff must be resumable without another blind append. Query hot/cold data using the published archive boundary so retained safety copies do not become duplicate logical rows.

### DuckDB hot data to Iceberg

Use the same seal → export → catalog commit → cross-engine verification order. Only then delete the sealed IDs from the local DuckDB hot table in a local transaction. The local DuckDB file is durable and owned by one writer process. Do not emulate ClickHouse TTL: use the explicitly tested retention/archive worker.

## Required live tests

| Test | Required evidence |
|---|---|
| Every normal data path | Complete common oracle in both writer and reader; exact snapshot and batch IDs |
| Multiple DuckDB appenders | Concurrent committed batches, retries/conflicts, 0 lost acknowledgements, 0 duplicate IDs |
| Reads during commits | Every observed version is a complete committed snapshot; report visibility delay |
| Catalog/storage restart | Previously acknowledged rows remain; ambiguous commit outcomes are reconciled, not silently replayed |
| Native ClickHouse replica failure | Producer/refresh continues, one replica killed and restored, all 9 copies verified, source/cache lag reported |
| Compaction | Measured file counts/sizes and query latency before/after; identical logical data and readable retained snapshot |
| Compactor crash | Failure during rewriting, current committed table still readable, safe subsequent rewrite |
| Archive failure before commit | Original hot rows remain; no destructive cleanup |
| Archive failure after commit, before deletion | Resume finds verified archive and avoids duplicate append; then safe deletion |
| Archive verification failure | Hot data remains and TTL/deletion is not armed |
| Full cleanup | Only this example's containers, volumes, images and state removed |

Overlapping updates, equality deletes and Iceberg v3 deletion vectors are separate compatibility experiments; v2 append interoperability is the baseline. Document unsupported pinned-version paths with the real error, rather than substituting Parquet export and calling it an Iceberg writer.

## Make workflow and output

One example stays under `iceberg/multi-system/`. Its five-command workflow is `make docker-up`, `make up`, `make test`, `make down`, `make docker-down`. `make test` runs all five architectures serially; `ARCH=...` selects one. Separate targets cover concurrency, compaction and each fault.

Every scenario writes ignored `results/<run-id>/<architecture>/` JSON: versions, limits, parameters, snapshots, row checks, commit/retry outcomes, p50/p99/max operation latency, query visibility lag, file metrics and recovery timings. README numbers come only from completed live runs. No synthetic success results.

## Guidance provenance and upstream references

- **Official:** DuckDB Iceberg writes require an attached catalog; separate processes can each attach that catalog. [DuckDB writes](https://duckdb.org/docs/current/core_extensions/iceberg/writing).
- **Official:** SeaweedFS has distinct S3 and Iceberg REST endpoints, OAuth support for DuckDB and atomic catalog commits. [Catalog](https://github.com/seaweedfs/seaweedfs/wiki/SeaweedFS-Iceberg-Catalog), [DuckDB setup](https://github.com/seaweedfs/seaweedfs/wiki/DuckDB-Iceberg-Integration), pinned `weed/s3api/iceberg/handlers_commit.go`.
- **Official:** ClickHouse's catalog writer uses catalog metadata updates. Inspect pinned `Databases/DataLake/RestCatalog.cpp` and `Storages/ObjectStorage/DataLakes/Iceberg/IcebergWrites.cpp`; first prove a ClickHouse commit is visible to DuckDB.
- **Official:** `icebergS3Cluster` schedules Iceberg files across compute nodes. [Cluster scans](https://clickhouse.com/docs/sql-reference/table-functions/icebergCluster).
- **Official:** Spark provides Iceberg data-file and manifest rewrite procedures. [Iceberg procedures](https://iceberg.apache.org/docs/1.10.0/spark-procedures/).
- **Derived:** Native serving generations and archive verification before hot-data deletion provide an explicit publication boundary. Their implementation and failure behavior still require live tests.
- **Rules applied:** `decision-ingestion-strategy`, `decision-late-arriving-upserts`, `schema-pk-plan-before-creation`, `schema-pk-prioritize-filters`, `schema-partition-lifecycle`, `schema-types-native-types`, `insert-batch-size`, `insert-mutation-avoid-update`, `insert-optimize-avoid-final`, `agent-discovery-schema`, `agent-query-safety`. Native ClickHouse keys follow customer/date filters; native partitions follow retention. MergeTree-specific rules do not describe Iceberg's own partitioning or file maintenance.
