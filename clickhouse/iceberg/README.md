# ClickHouse with Iceberg

One ClickHouse server writes and reads catalog-managed Iceberg on SeaweedFS.
Two experiments share one deployment: direct lake writes with retained snapshot
reads, and verified archival from MergeTree before TTL deletes hot copies.
Spark owns Iceberg data-file and manifest compaction. No DuckDB server runs here.

## Quick start

Prerequisites: Docker Compose v2 and Make; allow 6 GiB RAM and 5 GiB free Docker
disk for images and this small workload. Run from this directory.

1. `make docker-up` — start SeaweedFS S3 storage and its persistent REST catalog.
2. `make up` — build the Go client and Spark maintenance image; start ClickHouse.
3. `make test` — run the direct-write and archive experiments serially with 2 million rows each.
4. `make down` — remove the ClickHouse application container, preserving infrastructure state.
5. `make docker-down` — remove this project's containers, network, volumes and built image tags.

Tests reset their own table; the archive experiment runs last. Raw JSON goes to
ignored `results/manual/direct/` and `results/manual/archive/`. Change counts with
`make test ROWS=4000000 CUSTOMERS=20000 BATCH_ROWS=100000`. `TARGET_FILE_BYTES`
defaults to 134,217,728. A small dataset can compact to a file smaller than this.

| Command | Experiment |
|---|---|
| `make unit-test` | Reject malformed/failed oracle responses; lost append acknowledgements must not cause replay |
| `make direct-test` | Catalog writes → compact → verify → append → old-snapshot read → restart/recheck |
| `make archive-test` | Export sealed hot data → verify → authorize TTL cleanup → compact/recheck |
| `make failure-test` | Before-export, after-commit and verification-stage failures; retained hot IDs and safe resume |
| `make benchmark QUERY_RUNS=20` | Rebuild each baseline, compact, then measure warm full/customer queries |
| `make compact RESULTS_DIR=results/manual/archive` | One-shot Iceberg maintenance |
| `make verify` | Verify the current archive (the final state of `make test`) |
| `make status` / `make cli` | Deployment status / ClickHouse CLI |

## Directory structure

```text
iceberg/
├── Makefile                 # lifecycle, direct/archive tests, failures, benchmark
├── docker-compose.yml       # ClickHouse, SeaweedFS and one-shot tools
├── app/                     # Go HTTP/REST client, archive gates and measurements
├── compactor/               # pinned Spark/Iceberg Docker image and maintenance job
├── config/                  # ClickHouse memory and thread limits
└── results/                 # ignored snapshots, journals and measurements
```

## Limitations by scenario

| Scenario | Useful for | Limitation |
|---|---|---|
| Direct writes | Catalog-managed lake writes plus retained snapshot reads | Beta ClickHouse write support; one writer, append-only schema. No uniqueness enforcement, concurrent commit or schema-evolution test. |
| Archive before TTL | Keep recent rows native while publishing portable historical data | TTL itself cannot export Iceberg. The application seals/verifies a period before deletion; automatic sealing, scheduling and late-event repair are omitted. |

One ClickHouse server and one SeaweedFS mini instance share one host/disk. Restart
checks establish persistence, not replicated HA or physical disk-loss tolerance.
Spark performs explicit maintenance; snapshot expiration and orphan-file cleanup
are omitted. Warm, compressible, memory-resident query results are local lab
observations; they do not predict cold S3 scans or production throughput.

The shared compactor Dockerfile retains the historical Spark 4.0.1 / Iceberg
1.10.1 defaults for this recorded example. Its release artifacts can now be
selected with checked build arguments, and reports read the actual loaded
Iceberg version/commit. The separately pinned current maintenance experiment is
in [`iceberg/multi-system`](../../iceberg/multi-system/README.md#current-maintenance-engine-check);
its offline result does not establish a current-version rerun of this example.

## Direct-write baseline

The Go client bootstraps an empty Iceberg v2 table through the REST API.
ClickHouse appends 20 batches of 100,000 rows, and Spark compacts those files.
After another 100,000-row append, both the latest table and its original pinned
snapshot are checked. A restart of ClickHouse and SeaweedFS must preserve both.
Queriers use the catalog's committed metadata location, never the latest-looking
JSON filename in an S3 listing. This is the baseline for the archival experiment,
not a second nearly identical deployment.

One-shot Go commands write unique `command-metrics-*.json` files before exit:
application elapsed time, workload exit status, native process CPU/peak RSS and
raw cgroup/process counters. Spark writes a unique resource report and sample
JSONL covering Python, its JVM and container counters around startup, work and
stop. Missing/empty device counters remain unavailable; syscall character bytes
are not physical disk I/O. These reports survive Compose's `--rm`; server costs
and full container lifetime require separate observations. The
[multi-system qualification](../../iceberg/multi-system/RESOURCE_REPORT_RESULTS.json)
ran both shared worker paths and five Spark jobs; all 22 workload tools retained
reports. The corrected process-tick aggregation also passed 15 controls in the
actual Spark image and a fresh 2.1-million-row compaction. Historical results
below do not include these new reports; this is not a rerun of this directory's
complete failure-test matrix.

The shared Spark worker now pins each file-membership and row-count observation
to one native Iceberg snapshot, avoiding mixed-head metrics during concurrent
appends. Quiescent jobs keep exact before/after row equality. An explicit
`COMPACTION_LIVE_WRITERS=true` permits growth while still checking the original
snapshot's row count; `REWRITE_MANIFESTS=false` isolates data-file maintenance.
Separate invocations can use an existing directory below `/results` through
`COMPACTION_RESULTS_DIR`. See the pinned
[TableScan API](https://iceberg.apache.org/javadoc/1.10.1/org/apache/iceberg/TableScan.html#useSnapshot(long))
and [time-travel semantics](https://iceberg.apache.org/docs/1.10.1/spark-queries/#time-travel-queries-with-sql).

Qualification on 2026-11-22: **43 worker controls passed**, plus native 20,000-row
quiescent checks and the multi-system live baseline. That baseline observed
**2,410,000 → 2,630,000 rows**, **241 → 24 files** and
**188,388,828 → 117,520,223 file bytes** while appenders ran. The worker retained
the starting snapshot and completed in **11.162 s**; its sampled cgroup interval
used **26.813 core-s**, with **887,955,456 bytes** lifetime peak charged memory.
These are functional observations with concurrent appends, not a compression
ratio or capacity benchmark. The subsequent multi-system crash case failed when
its separately capped 1 GiB SeaweedFS process was OOM-killed; that failure does
not qualify compactor recovery or rerun this directory's full archive matrix.

## Verified archive before TTL cleanup

```text
MergeTree hot data → seal old period → Iceberg export/commit → exact verification
  → authorize expiry → TTL deletes hot copies

Queries: recent hot rows UNION ALL verified cold rows
Maintenance: Spark data-file and manifest rewrites
```

Native `TTL ... TO DISK/VOLUME` moves MergeTree parts; it does not export Iceberg.
The Go worker owns that handoff. It preserves IDs, integer cents and the original
event date; only local expiry bookkeeping is excluded from the archive.

The deterministic dataset has two date partitions, 2020-01-01 and 2099-01-01.
`expire_at` initially lies in 2100. After verifying the sealed old partition in
Iceberg, the worker records its snapshot, changes expiry for that partition and
uses `MATERIALIZE TTL` to finish promptly. Natural TTL cleanup is asynchronous.
The expiry mutation is a rare sealed-period operation, not per-event ingestion.
Production writers must enforce the seal against late arrivals and schedule the
worker according to the real retention policy.

Resume reuses a complete, correct archive; it exports an empty archive; it rejects
a partial or inconsistent archive and retains hot data. Ambiguous commits are
reported, never blindly replayed. Iceberg does not enforce event-ID uniqueness.
The published old/recent query boundary prevents safety copies double-counting.
`failure-test` checks that all 2 million hot rows survive each injected failure;
the post-commit case restarts both services before resuming.

## Measured results

2026-11-14, Linux/aarch64 Docker VM, 11 CPUs / 24.42 GiB RAM. Limits: ClickHouse
2 CPUs/1.5 GiB (1 GiB server memory), Go 2 CPUs/2 GiB, Spark local[2]/2 GiB (1 GiB
driver), SeaweedFS 1 GiB; query memory 768 MiB. These are small, compressible
correctness workloads and single-run timings, not production throughput claims.

| Check | Direct writes | Archive |
|---|---:|---:|
| Input rows / customers | 2,000,000 / 10,000 | 2,000,000 / 10,000 |
| Initial total | 10,001,000,000 cents | 10,001,000,000 cents |
| Cold rows | 2,000,000 | 1,000,000 |
| Remaining hot rows | — | 1,000,000 |
| Data files before → after compaction | 20 → 1 | 10 → 1 |
| Manifests rewritten | 21 → 1 | 11 → 1 |
| Latest rows after extra append | 2,100,000 | — |
| Retained snapshot rows after append/restart | 2,000,000 | — |
| Missing / duplicate / invalid IDs | 0 / 0 / 0 | 0 / 0 / 0 |
| Injected failure gates passed | — | 3 / 3 |

The date-preserving archive handoff, explicit TTL materialization and post-delete
verification took 0.749 seconds in the consolidated run. JSON contains exact snapshots,
byte counts and procedure timings. The consolidated lifecycle, both experiments, restart checks and all three
archive failure gates passed.

## Benchmark

2026-11-14, Apple M4 Pro, Docker VM aarch64 (11 CPUs / 24.42 GiB). The resource caps above apply. Other example deployments were stopped. Each query has 2 warmups and 20 timed iterations on freshly prepared, compacted data; warm OS/object caches, ClickHouse result cache disabled. Times include HTTP round trips. Every result is checked against deterministic counts and cents. With 20 samples, p99 is the maximum; tail resolution is limited.

| Path | Logical rows | Full aggregate p50 (ms) | p99 (ms) | Customer filter p50 (ms) | p99 (ms) |
|---|---:|---:|---:|---:|---:|
| direct | 2,000,000 | 16.783 | 23.431 | 22.375 | 24.491 |
| cold | 1,000,000 | 7.862 | 9.143 | 5.835 | 7.132 |
| hot | 1,000,000 | 3.279 | 3.905 | 1.068 | 1.221 |
| union | 2,000,000 | 12.779 | 13.937 | 7.215 | 9.307 |

Bulk loading uses server-generated `INSERT SELECT`, 20 batches × 100,000 rows, with external journal fsync. It does not transmit 2 million application payloads. The archive load below covers native hot ingestion; export/verification/TTL is timed separately.

| Preparation | Rows | Load (s) | Generated rows/s | Batch p50 (ms) | p99 (ms) |
|---|---:|---:|---:|---:|---:|
| direct | 2,000,000 | 10.164 | 196,778 | 27.623 | 9622.190 |
| archive | 2,000,000 | 0.182 | 11,005,083 | 8.173 | 14.357 |

The first direct-write batch took 9.622 s; its warm-batch median was 27.623 ms.
The load total retains that startup outlier rather than hiding it in a warm rate.

Raw samples and preparation reports: `results/performance/benchmark-{direct,archive}/`. These tiny, highly compressible data fit memory. CPU, HTTP and catalog overhead matter; this is not a disk/network saturation or engine ranking benchmark.

## Versions, ownership and limits

ClickHouse 26.10.2.103, SeaweedFS 4.53, Spark 4.0.1 and Iceberg 1.10.1. Explicit
beta settings enable this ClickHouse release's catalog/write support. Spark's
`rewrite_data_files` and `rewrite_manifests` publish new metadata snapshots.
Snapshot expiration and orphan deletion are separate retention operations and do
not run here. Runtime JAR checksums are verified at image build.

No host ports are published. Internal SQL HTTP/S3/catalog ports are 8123/8333/8181;
credentials are public local-demo values. One server and one SeaweedFS mini node
share the physical host/disk. Restart checks do not demonstrate disk-loss tolerance
or HA. The [multi-engine example](../../iceberg/multi-system/README.md) reuses this
worker and compactor, adding independent DuckDB verification and sharded serving.

Official: [TTL](https://clickhouse.com/docs/guides/developer/ttl),
[catalog integration](https://clickhouse.com/docs/engines/database-engines/datalakecatalog),
[Iceberg procedures](https://iceberg.apache.org/docs/1.10.0/spark-procedures/),
[SeaweedFS catalog](https://github.com/seaweedfs/seaweedfs/wiki/SeaweedFS-Iceberg-Catalog).
Pinned source inspected: ClickHouse `RestCatalog.cpp` and `IcebergWrites.cpp`,
SeaweedFS `handlers_commit.go`, Iceberg `RewriteDataFilesProcedure.java`.

Applied rules: `schema-pk-prioritize-filters`, `schema-partition-lifecycle`,
`schema-types-native-types`, `insert-batch-size`, `insert-mutation-avoid-update`,
`insert-optimize-avoid-final`, `agent-discovery-schema`, `agent-query-safety`.
The archive-before-expiry protocol is an application design, not a native TTL
feature. Native sort/partition rules govern MergeTree; Iceberg has its own layout.
