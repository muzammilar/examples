# Iceberg multi-system architecture and test plan

Status: all five normal data paths and native replica recovery passed the historical
2026-11-14 run; see README.md for its measured numbers and limits. All five paths
also passed the isolated 2026-11-22 functional rerun, including compaction and
2,100,000-row native replica readback. A subsequent five-path run accounts for
all 22 short-lived tools and 14 serving/storage roles; full engine startup/exit
CPU and repeated capacity measurements remain incomplete. This is not a fresh
performance qualification. The first continuous
recovery run failed its snapshot-ID metrics recorder, despite complete body checks;
its preserved 2,480,000 acknowledged rows passed a separate corrected verification.
The fresh two-million-row crash case, all-history checks and guarded teardown now
pass: 2,490,000 acknowledged rows in the new table and 2,480,000 in the old table
were freshly checked through each engine. The original failed verdict remains
unchanged. See `LIVE_RECOVERY_RESULTS.json`. The broader fault/concurrency matrix below is a roadmap, not
a claim that every listed test has run.

## Shared components

- SeaweedFS 4.53 provides S3-compatible object storage and an Iceberg REST catalog, persisted on example-owned volumes. No MinIO.
- Iceberg v2 tables contain Parquet data. The REST catalog's committed metadata location is authoritative; readers must not choose the newest-looking JSON file from an object listing.
- Go clients use DuckDB's Go driver and the ClickHouse Go client. DuckDB processes use independent local databases and attach the shared catalog; no shared writable `.duckdb` file.
- A Spark/Iceberg maintenance job performs data-file and manifest rewrites through the same catalog. It owns compaction, independently of the current writer/reader engine.
- Default to actual DuckDB 2.0.1 through the official shared C ABI and Go binding v2.20000.0; keep native 1.5.5 / binding v2.10505.0 as a labeled historical regression lane. Existing normal-matrix results are historical. Pin and verify core, driver and extension versions separately; see `DUCKDB_VERSIONS.md`. ClickHouse 26.10.2.103 and SeaweedFS 4.53 remain fixed comparison components; separately qualify newer releases.
- Run architectures serially to keep resource use bounded. The nine-node ClickHouse topology is enabled only for its scenarios. All containers still share one host and physical Docker disk.
- Use only example-owned local Docker/Compose services and an internal network.
  Never read, mount or use host AWS configuration/credentials, metadata discovery
  or real cloud resources. SeaweedFS is the local S3 reference, not a Tigris test.

### Initial continuous recovery qualification

The first new case is a retained-volume SIGKILL/restart of the SeaweedFS mini
process, with two independent writers and DuckDB/ClickHouse readers kept live.
Mini hosts both S3 and the catalog; this case does not isolate those dependencies
or demonstrate high availability. It uses a new UUID table, 2,000,000 seed rows,
10,000 customers and 10,000-row batches, with durable external intent/outcome
journals and complete post-recovery exports through both engines.

The active profile is capped at 7 CPUs / 6.5 GiB across storage, query and four
workload processes. Seed/verification tools run after those actors stop, at a
whole-profile cap of 4 CPUs / 4.5 GiB. Do not overlap this with the nine-node
serving topology or another lab whose combined caps exceed the Docker budget.
Report native CPU, memory, disk bytes/I/O, object size/compression and phase-tagged
operation latencies; these functional runs are not capacity comparisons.

Exit-time resource reporting now covers all 22 tools in a fresh normal matrix:
Go process/cgroup counters and Spark's JVM/Python/container observations. A
separate post-test window ensures native server intervals; Docker events retain
tool lifetimes. The process-CPU tick correction replays original samples and
passes native controls plus a fresh 2.1-million-row compaction/readback. See
`RESOURCE_REPORT_RESULTS.json`. Keep application elapsed time distinct from
container lifetime; empty `io.stat` is unavailable and `/proc/self/io` character
counts are not physical disk I/O. Full engine startup/exit CPU, normal-table
snapshot compression and repeated capacity trials still need work.

Before destructive cleanup, preserve and reverify all recovery tables on the
current volume against their original witnesses, including earlier cases after
later faults. Failed or incomplete qualification must retain its native state
and diagnostic exports; a successful subset cannot replace the failed verdict.

### Next fault: interrupted compactor output (qualification in progress)

The controller, upload gate and snapshot/body inspectors are implemented. The
first native attempt failed in its healthy baseline, before fault injection:
wrapping Go's `http.NoBody` changed an empty signed multipart-init request's
framing and caused a local S3 `403 SignatureDoesNotMatch`. This was a harness
bug, not an Iceberg recovery failure. Its failed verdict and original sources
remain preserved. A separate read-only audit verified **2,670,000 acknowledged
rows** through both engines, with zero missing or wrong bodies; its one ambiguous
batch was absent and remains unacknowledged. The minimal framing fix passes
signed-request regression controls and a real bundled-SDK multipart round trip
against local SeaweedFS (**48,128 bytes**, exact-body verification). Neither that
transport check nor the data audit qualifies the pending compactor-crash test.

The next fresh case passed the forwarding baseline and independently validated a
held output, then killed Spark with positive exit-137/non-OOM fencing. During
the hold SeaweedFS exceeded its 1 GiB container limit and was OOM-killed; the
DuckDB reader subsequently aborted on a native manifest HTTP I/O exception.
This remains a failed combined-fault observation, not an isolated compactor pass.
The same storage container/volume restarted. Preserve its original source and
151-artifact seal, diagnostic full-body reconciliation with a bounded writable spill directory now
verifies 3,390,000 acknowledged rows per engine and 36 absent ambiguous batches,
while retaining failed qualification. Investigate backend memory growth and the native
reader's exception boundary, then qualify the intended isolated fault separately.
Do not archive or reset this newer failure through the unrelated pre-fault receipt.

Use a fresh UUID table with the two-million-row witness; never compact the
archived precision-failure table, whose exact native snapshot is sealed.
Keep independent readers and append writers direct to storage. Pin
`partial-progress.enabled=false` and one concurrent rewrite group, with enough
small files to require rewriting. Test data-file rewriting before adding a
separate manifest-rewrite interruption.

A compactor-only, example-owned S3 response gate can forward uploads to local
SeaweedFS, then withhold **all** successful final-upload acknowledgements for
new Parquet objects under this table's exact data prefix. Cover ordinary PUT
and multipart completion; an uploaded part alone is insufficient. Preserve
signed headers, path and query bytes, and prove the ungated baseline works first.
Do not gate readers, writers or their catalog commits. Independently check a
complete new object and valid Parquet footer before SIGKILL/fencing of the exact
compactor container. This interrupts native output close, rather than sleeping
and assuming that rewriting happened. Retain output candidates and fault logs.

The pinned source makes this boundary observable: Iceberg's
[`S3OutputStream.close`](https://github.com/apache/iceberg/blob/apache-iceberg-1.10.1/aws/src/main/java/org/apache/iceberg/aws/s3/S3OutputStream.java)
waits for single-object/multipart completion. Its
[`S3FileIOProperties`](https://github.com/apache/iceberg/blob/apache-iceberg-1.10.1/aws/src/main/java/org/apache/iceberg/aws/s3/S3FileIOProperties.java)
defaults to five S3 retries, so withholding one response is insufficient.
With partial progress disabled, the
[`rewrite action`](https://github.com/apache/iceberg/blob/apache-iceberg-1.10.1/spark/v4.0/spark/src/main/java/org/apache/iceberg/spark/actions/RewriteDataFilesSparkAction.java)
finishes rewrite groups before its commit. A REST commit gate is an alternative,
but must be labeled post-rewrite/pre-commit interruption.

Retain the starting snapshot and verify all acknowledged bodies after the fault.
Concurrent append snapshots may legitimately advance; require absence of the
compactor replacement commit, not an unchanged current snapshot. Reconcile the
authoritative catalog history before starting a new compaction attempt. Unknown
commit state remains ambiguous; the native
[`commit manager`](https://github.com/apache/iceberg/blob/apache-iceberg-1.10.1/core/src/main/java/org/apache/iceberg/actions/RewriteDataFilesCommitManager.java)
also avoids cleanup in that state. Do not expire snapshots or delete orphans in
this first test: the [maintenance guidance](https://iceberg.apache.org/docs/1.10.1/maintenance/)
warns that retention shorter than an in-flight write can corrupt tables. Measure
continuous service, output/commit state, catch-up, compression and resources;
the gate alone is not evidence of recovery.

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

Measure Parquet compression from the exact catalog metadata location and explicit
snapshot ID. Enumerate unique live data files, exclude tombstones and reject
unsupported active delete files. Do not glob the warehouse or filter manifests
to the latest sequence: older manifests can still contain live files. At the
pinned Iceberg extension `45163a28`, the source renders data-file content as
`EXISTING`, so the collector must validate and record native labels rather than
assume a `content='DATA'` filter. Native snapshot qualification now passes on the
2.48/2.49-million-row recovery tables; normal-table coverage remains separate.
[Pinned metadata implementation](https://github.com/duckdb/duckdb-iceberg/blob/45163a28/src/function/metadata/iceberg_metadata.cpp),
[content conversion](https://github.com/duckdb/duckdb-iceberg/blob/45163a28/src/core/metadata/manifest/iceberg_manifest.cpp).

Sum each row-group/leaf-column's compressed and uncompressed chunk sizes once.
Their weighted ratio measures encoded Parquet page compression, including page
headers; whole-object/footer bytes and allocated storage remain separate.
Reconcile file row counts with the complete body oracle and recheck the snapshot
after inspection. [DuckDB 1.5.5 footer metadata](https://github.com/duckdb/duckdb/blob/v1.5.5/extension/parquet/parquet_metadata.cpp),
[Parquet byte definitions](https://github.com/apache/parquet-format/blob/master/src/main/thrift/parquet.thrift#L861-L881).

## Whole-volume cold recovery

The example-owned `make cold-storage` now performs serial clean shutdown,
read-only whole-volume checkpoint, byte/metadata verification on a fresh owned
volume and original-service restart. The reviewed Make run passed 134 controls
and restored 146 files. The separate two-table native audit preserved all 6.06
million acknowledged rows per engine but failed its simultaneous dependency
shutdown. The retained-state rerun passed serial query-before-storage shutdown
with both services exiting zero; keep both outcomes. Keep the archive,
original volume, native witnesses and that failed verdict. See `COLD_STORAGE.md`.
Native replay is now a tracked `cold-readback` Make target: both 2.67-million-
and 3.39-million-ACK-row cases pass with 146 host controls. The lost-CLI-response
control proves the pre-registered verifier is fenced while its failed data verdict
is retained. Guarded cleanup now removes registered temporary resources and preserves database
volumes/archives. Cold upgrade to pinned SeaweedFS 4.54 passes both tables and
serial shutdown; a persisted intent blocks older-image reuse. The original failed-case archive receipt, history verification and application
teardown now pass with 193 host controls, while its operational verdict stays
failed. Original-volume/image deletion awaits explicit approval after automatic
review rejection. Next qualify fresh bounded-memory/capacity settings and
continuous compactor recovery; the cold result does not establish an OOM fix.

## Current maintenance qualification

`make build-current-compactor` and `make test-current-compactor` now qualify the
explicit Spark 4.1.4 / Iceberg 1.12.0 build on isolated tmpfs with no network or
retained database mounts. The final native Make run passes 200 controls and
complete bodies through 2-million-row compaction, a 100,000-row append and the
original retained snapshot. The noexec-scratch failure remains recorded; final
success is published only after Spark/sampler shutdown, positive fencing and
container removal. See `MAINTENANCE_RESULTS.json` for resource and storage numbers.

Before selecting this image for live faults, wire the maintenance override into
both lifecycle and controller selection, freeze its build pins, and update the
version-specific evidence/cleanup gates. Then run the actual S3/REST compactor
interruption against the fresh bounded 4.54 fixture. Offline Hadoop-catalog
success does not close that operational requirement.

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
