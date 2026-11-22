# DuckDB version evaluation

The current lane uses **DuckDB 2.0.1**, linked through its official shared C
library and Go binding `v2.20000.0`. The binding normally bundles 2.0.0;
`duckdb_use_lib`, the checked release archive and `SELECT version()` establish
that this runner actually uses **2.0.1**. The historical lane retains native
1.5.5 and binding `v2.10505.0`. Both currently target Linux ARM64 here.

`LAB_PROFILE=current` and `DUCKDB_VERSION=2.0.1` are the defaults. To run the historical regression, export
`LAB_PROFILE=historical DUCKDB_VERSION=1.5.5` before the lifecycle commands; do not mix versions silently
between commands. Each lane has its own image and Go dependency lockfiles.
Original native database files and failure witnesses remain unchanged.

The manifest-I/O engine comparison below holds **SeaweedFS 4.53**, ClickHouse 26.10.2.103 and the existing
Spark 4.0.1 / Iceberg 1.10.1 maintenance image fixed as the common storage and
reader baseline. It does not qualify the latest release of every component.
SeaweedFS **4.54** now passes separate cold-upgrade readbacks of both retained
tables on a verified restored copy; see `COLD_STORAGE.md`. The original 4.53
baseline remains intact. The 4.53 OOM has not been reproduced on 4.54, and cold
compatibility does not qualify continuous compactor recovery. The separate fresh
current fixture now uses **4.54 / ClickHouse 26.10.10.3 / Spark 4.1.4 / Iceberg
1.12.0** and passes the current recovery cases below. Fixed profile names keep
its images, volumes and result ledger separate from the retained historical data.

## Manifest I/O failure

Both engines read the same preserved Iceberg snapshot, containing **2,790,000
rows**, with **SUM(amount_cents) = 13,951,395,000**. An example-owned, read-only
HTTP proxy forwards signed requests to local storage, then closes data-manifest
GET connections after fsyncing the exact request witness. It does not stop the
object store or modify its data. Both healthy controls returned the expected
count and sum.

| Engine | Manifest connection failure | Process outcome | Contract result |
|---|---|---|---|
| 1.5.5 / Iceberg `45163a28` | Uncaught native I/O exception | `SIGABRT`, exit 2; no final application report | Failed |
| 2.0.1 / Iceberg `f979b5b28a` | SQL I/O error returned to Go | Exit 1; failed-query report written, no OOM or abort | Expected rejection |

The newer extension contains a [destructor exception guard](https://github.com/duckdb/duckdb-iceberg/blob/f979b5b28a044c49c38a26636ac01bc1f7047fa5/src/planning/scan_plan/iceberg_manifest_store.cpp#L467-L480).
The controlled native results support that specific improvement. They do not
establish hard timeouts, arbitrary-fault recovery or a performance winner. The
probe checks aggregates; complete-body verification is separate below.

Run `make manifest-io MANIFEST_CASE=<owned recovery case>` after a successful
`make storage-crash`. The default selector uses that case's successful final
verification. `MANIFEST_VERIFICATION=<UUID>` selects another successful verification;
`MANIFEST_INSPECTION=<UUID>` selects a passed `before` full-body snapshot inspection.
Diagnostic verifications are not accepted as this controller's input. The source
must use complete, aligned 10,000-row amount cycles. Source files and the original
case's process registry are retained; each probe copies only configuration into
its own ignored result directory. A native abort returns nonzero, including in
the historical lane.

The final tracked Make runs took **42.703 s (2.0.1)** and **42.627 s (1.5.5)**,
including 124 host controls, two process starts, resource sampling and fencing.
These are test-workflow durations, not query latencies. Compact results and raw
evidence hashes are in [`DUCKDB_VERSION_RESULTS.json`](DUCKDB_VERSION_RESULTS.json).

## Full-body readback on the current engine

After the retained storage container and Docker Desktop restarted, the current
engine and ClickHouse independently checked **3,390,000 acknowledged rows / 339
batches**, with no missing, duplicate or wrong bodies. All **36 ambiguous
batches were absent** and remain unacknowledged. Both exports have canonical body
hash `404308f8e350e15a3ca87416cd1f712b66f2506aed41b9d38eb1c41ba432b1c0`.

The 100-file snapshot occupied **176,839,475 file bytes**. Its encoded Parquet
column chunks contained **278,150,885 uncompressed / 176,666,496 compressed
bytes (1.5744×)**. This is not logical CSV-to-disk compression.

Verification `0a1b3af7-e6c1-4aba-84db-cef69b0eb4e9` records actual native **2.0.1**,
binding `v2.20000.0`, platform and all three extension revisions/binary hashes in
its own `native-versions.json`, hash-bound to the verification and storage reports.
It retains **`qualified=false`, `integrity_passed=true`**: the original 1.5.5
reader crashed during the earlier combined fault. A newer readback cannot turn
that old availability failure into a pass.

## Current-version interoperability failure

The actual `make probe DUCKDB_VERSION=2.0.1` on 2026-11-22 failed when DuckDB
read the fresh ClickHouse-written `lab.reverse` table:
`Invalid Configuration Error: Manifest ... has invalid Avro key-value metadata, content is missing`.
The DuckDB-written 10,000-row table was already readable by ClickHouse with the
correct sum; the reverse 20,000-row read did not pass. This is a separate
compatibility failure, not the manifest-connection failure above. The
[Iceberg manifest specification](https://iceberg.apache.org/spec/#manifest-file-format)
requires the `content` header for v2/v3 manifests. Independent native inspection using Spark's bundled Avro `DataFileStream` confirms
both tables declare format version **2**. The DuckDB-written manifest contains
`content=data`; the ClickHouse-written manifest has seven header keys and omits
`content`. The reader is rejecting a missing required header in this tested
ClickHouse output. No committed object was rewritten by the inspection.
Do not patch committed manifests, weaken validation or report the five-path
2.0.1 matrix as passed. Historical 1.5.5 normal-path results remain historical.
The fresh current fixture reproduces the same failure with **ClickHouse
26.10.10.3 / SeaweedFS 4.54**. See the independent header/body inspection in
[`CURRENT_INTEROP_RESULTS.json`](CURRENT_INTEROP_RESULTS.json) and the unposted
[`upstream report draft`](CLICKHOUSE_MANIFEST_ISSUE.md).

## Fresh current recovery and supported paths

The current Make workflows pass DuckDB direct ingestion, archive handoff and
the three-shard/three-copy native ClickHouse serving path. Each begins with
**2,000,000 rows / 10,000 customers**; replica recovery verifies **2,100,000**
logical rows and placement on all nine copies. ClickHouse-written paths remain
blocked by the conformance failure above.

Retained-volume storage-process recovery reconciles **2,400,000 acknowledged
rows**; two ambiguous batches remain absent. A fresh Spark compactor-output crash
and subsequent native recovery reconcile **3,910,000 acknowledged rows**, with
independent readers and writers live throughout. Both engines verify every body.
These checks use Spark for maintenance; they do not qualify DuckDB-native
compaction or every failure boundary.

The separate `make duckdb-maintenance` baseline now passes healthy native
v2 data-file rewriting on **2,000,000 rows / 10,000 customers**: rollback,
**20 → 1 live files**, retained snapshot, no-op, a wrong-body control and
independent ClickHouse/REST verification. Its current Make run passes **215
controls**; the committed CALL profile is **2,149.995 ms**. These are functional
observations, with CPU/memory/compression/disk/I/O in
[`DUCKDB_MAINTENANCE.md`](DUCKDB_MAINTENANCE.md). Native live-fault recovery
remains unrun. A separate [controlled append conflict](DUCKDB_CONFLICTS.md) rejects
the stale rewrite with `Conflict_409`, preserves 110,000 acknowledged rows and
passes fresh-transaction replanning; continuous-writer progress is still unrun.
Separate native ten-row controls
now verify v3 and pending data/schema rejection, unchanged committed metadata and
complete bodies; `make duckdb-rejections` passes 217 controls. See
[`DUCKDB_REJECTIONS.md`](DUCKDB_REJECTIONS.md) for pinned client settings and resources.

The earlier current compactor case remains failed: its **3.8-million-row** final
ClickHouse ordered export exceeded the **1 GiB native server cap**. Read-only
controls against that exact snapshot establish external-sort spilling as the
observed fix; reducing the Parquet budget alone failed. A bounded diagnostic
export passes while preserving the original failed verdict. See
[`CURRENT_RECOVERY_RESULTS.json`](CURRENT_RECOVERY_RESULTS.json) and
[`CLICKHOUSE_READ_MEMORY_RESULTS.json`](CLICKHOUSE_READ_MEMORY_RESULTS.json).

## Maintenance dependency qualification

The separately selected **Spark 4.1.4 / Iceberg 1.12.0** image now passes an offline
2-million-row compaction/append/retained-snapshot Make check with full bodies,
200 recovery controls and explicit resource measurements. Its later integration
into the fresh current S3/REST fixture also passes the compactor-crash case above;
the offline check alone does not establish that result.
See [`MAINTENANCE_RESULTS.json`](MAINTENANCE_RESULTS.json).

Spark's [release page](https://spark.apache.org/downloads.html) lists 4.3.0 as newer,
but the [Iceberg 1.12.0 integration modules](https://github.com/apache/iceberg/blob/apache-iceberg-1.12.0/settings.gradle)
have no 4.3 implementation. Maven Central returned 404 for the 4.2 runtime's
release checksum, while the 4.1 runtime and AWS bundle were available. Both JARs
are SHA512-checked, and the Spark image is digest-pinned. The compactor now reports
the loaded [Iceberg build version and commit](https://github.com/apache/iceberg/blob/apache-iceberg-1.12.0/api/src/main/java/org/apache/iceberg/IcebergBuild.java),
with matching release references, instead of a hardcoded version string.

## Remaining qualification

- Resolve/retest ClickHouse-written manifest compatibility before qualifying the
  complete current-version normal architecture matrix.
- Preserve the earlier current memory-failed case and close all-history
  verification; keep teardown fail-closed until the required evidence exists.
  Current-fixture cold backup/restore now passes 10.11 million acknowledged rows
  per engine across all three witnessed tables, retaining the failed verdict;
  see `CURRENT_COLD_RESULTS.json`. Do not assume in-place downgrade compatibility.
- Rerun Quack deadlines, native WAL recovery and owned-copy file-format migration
  on 2.0.1. Historical 1.5.5 timeout and beta-protocol findings do not establish
  current Quack behavior.
- Run repeated, equivalent cold/warm and sustained resource/performance trials.
  These functional diagnostics provide no current-version capacity ranking.

See the official [2.0.1 changes](https://duckdb.org/2026/11/20/announcing-duckdb-201)
and [Go native-library linking instructions](https://github.com/duckdb/duckdb-go/blob/v2.20000.0/README.md#linking-a-dynamic-library).
