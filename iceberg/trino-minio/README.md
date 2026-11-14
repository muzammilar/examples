# Iceberg, Trino and MinIO: snapshot recovery

One million Parquet orders in Iceberg, queried by a Trino coordinator and three workers. Four MinIO storage nodes supply erasure-coded object storage. Separate tests exercise worker loss, one storage-node loss and catalog persistence.

## Quick start

1. `make docker-up` — build pinned MinIO sources and start the storage, catalog and Trino services.
2. `make up` — build the Python client and commit 10,000 customers and 1,000,000 orders to Iceberg.
3. `make test` — reconcile the snapshot, save the failure-test checksum and run order-history/schema-evolution analytics.
4. `make down` — drop the example tables and schema.
5. `make docker-down` — delete this project's containers, volumes and locally built images.

Requires Docker Compose v2, Make, Python 3 for fault-control JSON parsing, and network access for source builds. Allow 10 GiB RAM and at least 10 GiB free Docker disk space. The first MinIO source build takes several minutes. All services run natively on the tested aarch64 Docker VM.

## Setup

| Component | Version / configuration | Host access |
|---|---|---|
| Trino | 484; coordinator + 3 workers; 1 GiB JVM heap per process | `127.0.0.1:18080`, override `TRINO_PORT` |
| Catalog | Apache Iceberg REST fixture 1.10.1; JDBC SQLite metadata on a named volume | Internal `catalog:8181` |
| MinIO | Source release `2025-10-15T17-29-55Z`; 4 nodes / 4 volumes | HAProxy at `127.0.0.1:19000`, override `S3_PORT` |
| MinIO client | Source release `2025-08-13T08-35-41Z` | Internal bucket initialization |
| Buckets | `warehouse` for Iceberg, `exchange` for Trino task recovery | Local credentials in Compose |
| Workload | Python 3.12.11; standard-library HTTP, JSON and filesystem APIs | Docker-built client |

The catalog fixture is deliberately a local, single-process catalog. Its SQLite database is persistent; it has **no catalog failover**. Trino also has one coordinator. Neither is a production HA design. MinIO has four storage processes with default two-parity erasure coding; they still share one physical Docker disk.

`retry-policy=TASK` and the filesystem exchange manager persist Trino intermediate exchanges to the separate S3 bucket. The task memory estimate is reduced to 128 MiB for this local setup, rather than the multi-GiB default. Native S3 and exchange S3 settings both use path-style access and the internal HAProxy endpoint. Source archives are SHA-256 checked in `minio/Dockerfile`; the old public MinIO Docker Hub images were unavailable during implementation.

## What is checked

`make up` generates deterministic IDs 0–999,999, customer `(id / 100) % 10000`, and amount `id % 10000 + 1` cents. Orders use Parquet with 16 customer hash buckets. A successful CTAS is one acknowledged Iceberg snapshot, rather than one million independent SQL transactions.

The client fsyncs the acknowledged query ID, row range and snapshot ID to the host-mounted `results/` directory. Failure tests retain that witness and never recreate or reseed the tables. Reconciliation checks count, distinct IDs, minimum, maximum, every expected customer/amount, the customer join, sum, and presence of the original snapshot. With the complete deterministic range this detects any missing or duplicate ID. It exits nonzero on any mismatch.

The longer query expands each order `AMPLIFY=256` times and hashes `(op_id, expansion_id)`: **256,000,000 hash inputs**, with a reference count, amount sum and checksum. This keeps a worker active long enough to inject a fault. It is a recovery workload, not a representative analytical performance benchmark.

| Command | Experiment |
|---|---|
| `make failover` | Find a RUNNING task through Trino's query metadata, SIGKILL that worker for 10 seconds, restore it, compare the recovered query to the saved reference and reconcile the original snapshot |
| `make chaos-minio` | During the same active query, kill `minio1` for 10 seconds, restore it and repeat both comparisons |
| `make chaos-catalog` | Kill/restart the single REST catalog, then verify its persisted metadata and the acknowledged rows; this measures restart persistence, not live catalog failover |
| `make test AMPLIFY=32` | Shorter 32-million-input query; use the same `AMPLIFY` for its corresponding fault test |
| `make status` / `make cli` | Membership / interactive Trino shell |

Defaults: `COUNT=1000000 CUSTOMERS=10000 AMPLIFY=256`; set counts on `make up`, which replaces this example's tables. Run `make test` before the fault targets to save the checksum reference. Each invocation uses a timestamped result directory; choose a fresh `RUN_ID` if overriding it.

## Representative workload: order history and corrected analytics

`make test` now runs this scenario; `make order-history` reruns it after provisioning. It uses the Trino SQL client and persistent Iceberg/MinIO stack rather than expanding rows for the recovery hash query.

1. Copy the original **1,000,000 orders** into a separate bucket-partitioned history table and capture its snapshot ID.
2. Add a nullable `channel` column without rewriting the old rows; append **100,000 orders** tagged `web` and capture another snapshot.
3. Correct amounts for customer 42, verify the current **1,100,000 distinct orders**, and query both earlier snapshots with `FOR VERSION AS OF`.
4. Copy the current state to an unpartitioned table and compare identical tenant dashboards on both layouts. Every query must return **200 orders / 852,100 cents**.

The final current total was **5,500,552,000 cents**; the original snapshot still returned **1,000,000 orders / 5,000,500,000 cents**, and the pre-correction append snapshot returned **1,100,000 orders / 5,500,550,000 cents**. The original million rows had null `channel`, while all 100,000 new rows had `web`. The original recovery-test `orders` table and witness are kept separate from the history tables.

2026-11-14, Apple M4 Pro, shared native aarch64 Docker VM, 11 vCPUs / 24.4 GiB RAM, three Trino workers with 1 GiB heaps, no CPU caps. Final run `20261114T191822Z`, 20 sequential dashboard queries per layout.

| Operation | Measured wall time |
|---|---:|
| Create million-row history snapshot | 1.715 s |
| Add nullable column | 0.042 s |
| Append 100,000 orders | 1.890 s |
| Correct 200 customer orders | 0.920 s |
| Read original snapshot totals | 0.412 s |
| Read pre-correction snapshot totals | 0.484 s |

| Tenant dashboard layout | Physical input positions/query | Input bytes/query | p50 / p99 / max |
|---|---:|---:|---:|
| 16 customer hash buckets | 66,900 | 129,243 B | 256.398 / 653.912 / 653.912 ms |
| Unpartitioned current-state copy | 1,100,000 | 2,009,199 B | 93.919 / 442.007 / 442.007 ms |

Bucketing read 16.44× fewer physical input positions and 15.55× fewer bytes. Its p50 was nevertheless 2.73× higher in this small run. The layouts have different file histories: the bucketed table retains the correction/delete path, while the unpartitioned copy is freshly rewritten. Task scheduling, file counts, caching and delete processing matter alongside bytes scanned. This is a measured workload tradeoff, not evidence that bucketing always improves latency.

The system's useful capability here is persistent historical versions, schema evolution and table-level analytics over shared object storage. It is not a replacement for a low-latency transactional row store. Snapshot expiration, compaction policy, concurrent correction conflicts and multiple independent query engines are not exercised by this scenario. The separate [20-million-row engine benchmark](../benchmarks/) already covers another Iceberg analytical workload; the [multi-system design](../multi-system/) remains a plan.

`HISTORY_QUERIES=20` is configurable (2–100); `COUNT` controls the original rows and the append is one tenth of that count, rounded down with a minimum of one. `order-history.json` records all three snapshot IDs, operation timings and per-query physical input metrics. Full query JSON is beside it. The full five-command lifecycle, including removal of both history tables, passed. Twenty sequential observations do not establish stable production tail latency.

Sources: [Trino 484 Iceberg snapshots, schema evolution and partitioning](https://trino.io/docs/484/connector/iceberg.html), [484 snapshot selection and add-column implementation](https://github.com/trinodb/trino/blob/484/plugin/trino-iceberg/src/main/java/io/trino/plugin/iceberg/IcebergMetadata.java). All snapshot contents, timings and read metrics above were measured locally.

## Verified test coverage

These scenarios ran live. Every reconciliation retained the original acknowledged snapshot with **1,000,000 observed rows**, **0 missing acknowledged IDs**, **0 duplicates**, and **5,000,500,000 cents**. The worker-loss query recovered 3 failed tasks within the same query ID and matched its reference checksum.

| Executed scenario | Local run ID |
|---|---|
| Commit and reconcile 1,000,000 orders | `20261114T181015Z` |
| 32-million-input reference query | `20261114T181059Z` |
| 256-million-input reference query | `20261114T181205Z` |
| Running-worker failure | `20261114T181432Z` |
| MinIO node failure | `20261114T181534Z` |
| Catalog restart/persistence | `20261114T181558Z` |

Other row-count/customer-count combinations were not run. Coordinator failure, catalog HA and online compute scaling are not implemented tests. The initial fault-harness registration/selection attempts failed before injection and are excluded from the successful coverage above.

Raw evidence remains in the Git-ignored `results/<run-id>/` directories: `acknowledged.json`, `reconciliation.json`, `heavy.json`, `ready.json` and `failover.json` as applicable. Query elapsed times and shared-VM conditions are below. Snapshot CTAS timing is not directly comparable to the transactional Go-client benchmarks.

## Measured results

2026-11-14, Apple M4 Pro, shared aarch64 Docker VM with 11 vCPUs / 24.4 GiB RAM, native images, no CPU caps, 1 GiB Trino heaps. One run per scenario; other database examples and builds shared the VM.

| Scenario | Query elapsed | Failed tasks | Result / recovery |
|---|---:|---:|---|
| Commit 1,000,000 orders | 4.713 s | — | Acknowledged snapshot `8433053621958082125` |
| Reconcile rows | 2.940 s | — | 1,000,000 distinct IDs; 5,000,500,000 cents; 0 bad values |
| 32-million-input query | 3.214 s | — | Checksum `43C489C2478DA766` |
| 256-million-input reference | 8.105 s | — | Checksum `1CB939FB1EA94115` |
| Running worker killed | 54.199 s | 3 | Same 256-million-input checksum; worker outage plus rejoin 34 s |
| MinIO node killed | 13.349 s | 0 | Same checksum while surviving nodes served storage; node outage plus rejoin 16 s |
| Catalog restarted | reconciliation 4.452 s | — | Original snapshot and all 1,000,000 orders remained |

Each failure test observed **1,000,000 original orders**, **0 missing acknowledged IDs**, **0 duplicate IDs**, and **5,000,500,000 cents**. The amplified query returned **256,000,000 rows counted** and **1,280,128,000,000 cents**. The worker test took 6.69× its reference query time; this includes retry delays and changing shared-VM contention. The client did not resubmit the worker-failed query: Trino recovered its tasks within the same query ID.

Raw `heavy.json` includes query and stage statistics; `ready.json` identifies the selected running task's worker; `failover.json` records restart timing; `reconciliation.json` records counts and snapshots. Results are ignored by Git. Initial harness attempts failed before fault injection because queued queries are not immediately registered and Trino 484 exposes a flat stage collection; both paths are now handled.

## Limits and references

Compute task retries preserve this read query. Coordinator loss, concurrent writers, partially acknowledged write commits, full object-store loss, catalog HA, backup restore, and online compute scaling are not tested. The CTAS witness proves snapshot persistence for the injected process failures, not durability across losing the shared host disk. Hoglake remains documentation backlog only.

- [Trino 484 fault-tolerant execution](https://trino.io/docs/484/admin/fault-tolerant-execution.html) and [exchange configuration](https://trino.io/docs/484/admin/properties-exchange.html).
- [Trino 484 Iceberg REST connector](https://trino.io/docs/484/connector/iceberg.html#rest-catalog).
- [Trino 484 REST catalog implementation](https://github.com/trinodb/trino/blob/484/plugin/trino-iceberg/src/main/java/io/trino/plugin/iceberg/catalog/rest/TrinoRestCatalog.java).
- [Iceberg 1.10.1 REST fixture source](https://github.com/apache/iceberg/tree/apache-iceberg-1.10.1/open-api).
- [Pinned MinIO erasure-server source](https://github.com/minio/minio/blob/RELEASE.2025-10-15T17-29-55Z/cmd/erasure-server-pool.go).
