# Iceberg shared storage, multiple engines and archive handoffs

DuckDB, ClickHouse and Spark operate on catalog-managed Iceberg v2 tables on
SeaweedFS. This example belongs under Iceberg because it tests interoperability.
The single-server, ClickHouse-only examples are separately indexed under
[`clickhouse/`](../../clickhouse/README.md#iceberg-examples).

## Quick start

Prerequisites: local Linux Docker with Compose v2, Make and Python 3.11+ (including
the `python3` selected by Make). Recovery controls require a local Unix Docker
socket/context. Allow 20 GiB Docker RAM
and 12 GiB free Docker disk including images/build cache. The native serving case runs nine
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
`make cluster-down` stops the native serving tier and Keepers, retaining their
volumes and the storage/query services. Run it after the normal matrix before
`make test-production`; the separate recovery profile refuses other active
topologies. Run `make verify-history` again after subsequent faults, before
teardown, to recheck earlier recovery acknowledgements.

Normal Go commands also save unique `command-resources-*.json` reports; the
shared archive worker uses `command-metrics-*.json`. They retain success/failure,
elapsed time, native process CPU/peak RSS and raw cgroup/process I/O counters
before Compose removes the tool. Spark saves `compaction-<UUID>-resources.json`
and its sample JSONL, including separate Python and JVM observations and startup/
stop outcomes. The reported application intervals exclude container startup and
final teardown; Docker events provide container lifetime separately. Engine
memory is not just the Go/Python heap. Empty device counters mean unavailable,
not zero. Server sampling remains separate. This reporting passed a fresh
five-architecture functional run; it does not retroactively fill earlier samples.

Resource reporting qualification (2026-11-22): actual `make test` passed in
**92.931 s**, with **22 tool reports** (15 runner, 2 archive-worker, 5 Spark).
Docker start/die events independently captured all 22 successful workload
lifetimes; a separate 0.088-second unit-control container is excluded explicitly.
All 14 serving/storage roles also have native resource intervals, including a
separately labeled quiescent post-test window. This is functional resource
accounting, not a capacity or architecture ranking. See
[`RESOURCE_REPORT_RESULTS.json`](RESOURCE_REPORT_RESULTS.json).

| Compaction case | Lake rows | Observed cgroup CPU, core-s | Lifetime charged-memory peak, MiB |
|---|---:|---:|---:|
| DuckDB direct | 2,000,000 | 13.632 | 772.9 |
| ClickHouse direct | 2,000,000 | 12.962 | 769.6 |
| ClickHouse archive | 1,000,000 | 11.284 | 671.2 |
| DuckDB archive | 1,000,000 | 11.329 | 805.0 |
| Native serving source | 2,000,000 | 12.556 | 740.0 |

CPU deltas cover observed intervals after Python entry, not all JVM startup/exit
costs; memory peaks are native container-lifetime values including file cache.
Python-only memory would omit most of the engine. The first report exposed
coarse 100-Hz process-CPU ticks producing a false short-interval spike. Original
reports remain intact; corrected aggregation replays the same five raw streams,
retaining CPU totals/I/O and counting suppressed utilization estimates below ten
ticks. All 15 controls passed under the actual Spark image's Python 3.10.12.
The corrected image then compacted **2,100,000 rows** in a separate native check,
retained the old snapshot and passed both-engine verification; only aggregation
changed after the full five-path run. Cleanup passed and nine unrelated
containers stayed unchanged. Normal-path encoded compression, full startup/exit
cost and repeated cold/warm capacity trials remain incomplete.

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

The current nine-copy serving profile assigns **0.5 CPU per data server**
(`CLUSTER_DATA_CPUS`, default `0.5`), 0.5 per Keeper, 2 to the query coordinator,
1 to storage and 2 to the active workload/compactor. The serial full-stack configured
budget is **11 CPUs / 16.5 GiB**, with no extra container swap, fitting this 11-CPU
Docker VM; do not overlap it with
another lab. Larger per-node overrides require a larger verified CPU budget.
Historical November 14 results below used 1 CPU per data server and must not be
treated as measurements of this revised budget. This resource change does not
alter the three shards or three replicas per shard.

## Directory structure

```text
multi-system/
├── Makefile                 # five-command lifecycle, matrix, benchmark
├── docker-compose.yml       # SeaweedFS, coordinator and Dockerized clients
├── docker-compose.recovery.yml # bounded live writers/readers and one-shot tools
├── app/                     # DuckDB load/archive, cross-engine oracle, native generations
├── config/                  # query limits; generated 3-shard/3-replica/Keeper topology
├── scripts/                 # topology renderer and architecture matrix runner
├── recovery-contract.json   # declared semantics, limits and reference client
├── recovery_control.py      # native fault, phase/progress and workload lifecycle
├── recovery_cleanup.py      # fixture index, all-history verification and teardown
├── recovery_resources.py    # native CPU, memory, disk/I/O sampling
├── recovery_report.py       # offline operation/latency/compression summaries
├── test_recovery_*.py       # controller, report and cleanup negative controls
├── PLAN.md                  # unimplemented experiments, explicitly separate from results
└── results/                 # ignored JSON metrics and acknowledgement journals
```

`clickhouse-archive-workload/` and `iceberg-compaction/` are source symlinks to
the repository-tracked ClickHouse archive worker and Spark compactor. `make up`
and `make unit-test` first run the local `prepare.py`, which materializes only
tracked regular files into ignored `.build/` contexts, using hard links where
supported and copies otherwise. Docker builds from those local contexts; it does
not follow directory symlinks automatically. No sibling Makefile, generated
files, running services or previous state are required. Commit the source links
and preparation script, never `.build/`.

Build-isolation verification (2026-11-22): all seven materialized files match
tracked source bytes and are regular hard links on this host; generated files
are ignored and a symlinked build destination is rejected. The archive-worker
Go unit suite passes from this local context with networking disabled and
2-CPU/1-GiB limits; all three workload build contexts resolve inside this example.
That checkpoint validated preparation and existing controls; the later live
data-path and recovery results are recorded separately below.

Initial isolation checkpoint (2026-11-22, before live qualification): the Compose
network is now internal, workload metadata discovery is disabled, and the Go
runner rejects endpoint/profile/credential-discovery overrides before opening
an engine. Credentials remain explicit lab values; host AWS configuration is
never mounted or used. Standalone standard-library guard tests pass, and all
**17 rendered services** remain on the internal network without forwarding
synthetic host credential variables.

The committed isolation source (`7453215`, before the recovery-workload additions)
also passed full-package Go tests, native ARM64 compilation and two real binary
rejection probes inside a Docker SDK container with **networking disabled**.
The synthetic non-local endpoint and profile overrides were rejected before
engine setup. Limits were **1 CPU / 2 GiB**, with **75.533 CPU core-seconds** and
**823.84 MiB peak charged memory** for tests/build/probes together. These are
compiler/control costs, not engine benchmark numbers. Public dependency download
ran separately without host AWS configuration or credentials; both SDK containers
were removed after healthy stopped-state/ownership checks.
[Source/image/counter evidence](LOCAL_ISOLATION_RESULTS.json) is compact; raw logs,
compiled binary and caches stay ignored under `.build/`.

The initial recovery-runner runtime image also built successfully. In a network-disabled
container, DuckDB 1.5.5 loaded its preinstalled HTTPFS, Avro and Iceberg extensions;
three actual entry-point rejection probes passed. The bounded image builder used
**90.972 CPU core-seconds** between counter snapshots and peaked at **3.75 GiB**
charged memory under a 2-CPU/4-GiB cap, with no OOM. Runtime probes peaked at
**69.38 MiB** under 1 CPU/2 GiB. Source/binary and extension hashes are recorded in
[`RUNTIME_PREPARATION_RESULTS.json`](RUNTIME_PREPARATION_RESULTS.json), including
preserved failed helper attempts. These are build/control costs, recorded before
the five-path rerun and live recovery qualification. Runtime extensions/JARs
must be installed during image build, since the running lab has no outbound
network.

Native preparation then passed for SeaweedFS **4.53 / 4ab66fb76** and ClickHouse
**26.10.2.103**: owned internal networks, actual CPU/memory/swap caps, native
resource probes and one unreleased workload container's source/image/witness
checks. The probe container was stopped and removed without opening its START
gate. Authenticated local catalog discovery and `make verify-history` passed on
the **empty** fixture, along with **53 host controls**. Make used an explicit
Python 3.14 PATH prefix after preserving a Python 3.9 prerequisite failure.
Idle probes sampled about **4.1 seconds**, with **129.70 MiB** storage and
**546.72 MiB** query lifetime peak memory; these include setup and are not load
benchmarks. [`NATIVE_PREFLIGHT_RESULTS.json`](NATIVE_PREFLIGHT_RESULTS.json)
retains counters, image digests and the earlier Desktop bind-alias failure.
The separate guarded `make down` and `make docker-down` commands then passed for
this empty fixture. Final native inventories found no owned containers, volumes,
network or built image tags; all preflight evidence remains. This validates empty
fixture cleanup, not acknowledged-data recovery or data-bearing history teardown.
These initial preparation results precede the current-snapshot Parquet
instrumentation. Its updated image has now passed the canonical Docker build,
native local-Parquet SQL checks, offline extension loading and entry-point guards.
The bounded 2-CPU/4-GiB builder used **89.145 CPU core-seconds** between counter
snapshots and peaked at **3.67 GiB** charged memory. Its state and probe containers
were removed. The local SQL control validates footer
APIs, not an Iceberg/S3 workload compression result.
[`STORAGE_RUNTIME_RESULTS.json`](STORAGE_RUNTIME_RESULTS.json) records the 15-file
source freeze and counters. This preparation preceded live snapshot inspection
and recovery qualification.

## Live recovery qualification (2026-11-22)

`make storage-crash` / `make test-production` exercise a separate,
UUID-named DuckDB → Iceberg → ClickHouse workload. The corrected live crash case
and fresh checks of both recovery tables **passed**; the full production matrix
and comparative performance qualification remain incomplete. See
[`LIVE_RECOVERY_RESULTS.json`](LIVE_RECOVERY_RESULTS.json) and
[`NORMAL_PATH_RESULTS.json`](NORMAL_PATH_RESULTS.json) for curated evidence.
On 2026-11-22, all five normal paths passed a fresh functional rerun with
2,000,000 input rows per case, Spark compaction and 2,100,000-row native replica
readback after an append/repair. The initial invocation failed a host-test fixture
assumption about `RESULTS_DIR`; its corrected rerun passed in 91.545 s. Sampling
missed several short-lived tools and most serving-node CPU/I/O intervals, so this
does not qualify a new full-stack performance comparison.

The first live crash run failed its metrics recorder: decoding a catalog response
through floating point rounded the 64-bit snapshot ID. Both engines nevertheless
verified all **2,480,000 acknowledged rows**, with zero missing/wrong bodies;
two ambiguous batches were absent. A subsequent collector attempt exposed a
case-sensitive `parquet` format check. Both bugs now have native Go regression
tests. A separate corrected, read-only verification preserved the same table UUID,
metadata location and exact snapshot ID, and checked all bodies again. Its
248 live Parquet files contain **193,916,505 bytes**; encoded column chunks total
218,309,192 uncompressed versus 193,607,993 compressed bytes (**1.128×**).
This ratio excludes file/footer overhead and is not logical-input compression.
Original failures and exports remain preserved; this corrected data check does
not replace the original failed verdict.

The fresh corrected `make test-production` passed in **327.565 s** with frozen
sources. It verified **2,490,000 acknowledged rows** (2,000,000 seed plus 490,000
live writes) through each engine. All 17 ambiguous batches and 40 pre-submission
failed batches were absent; no acknowledged body was missing, partial, duplicate
or wrong. After this outage, both engines freshly reverified this table and the
earlier 2,480,000-row table: **4,970,000 acknowledged rows per engine**.
The new table has 249 live Parquet files / 194,661,254 bytes, with **1.128×**
encoded column-chunk compression. The owned storage process was held down for
20 seconds; phase timings include inspection and startup overhead. Query survivor
identity stayed unchanged. Historical cached reads during outage do not establish
fresh-data availability.

| Observation | Baseline | Recovered |
|---|---:|---:|
| Successful 10,000-row append requests | 23 | 21 |
| Append p50 | 200.896 ms | 200.198 ms |
| Append p99 | 245.555 ms | 265.612 ms |
| Successful append requests/s | 1.600 | 0.756 |

These are paced fault-workload observations, not capacity. Phase attribution uses
completion time; requests crossing boundaries are separately labeled in JSON.
Recovered-phase ambiguous attempts remain in error counts instead of being hidden
by retry. Small p99 sample counts are effectively maxima. CPU samples recorded
34.133 core-seconds for ClickHouse and 26.430 for storage; their native lifetime
charged-memory peaks were 1,611,137,024 and 1,074,118,656 bytes respectively.
Those peaks can include earlier work in the same containers. Per-role/per-phase
CPU, charged memory, allocated disk, block read/write bytes and IOPS are in the
report; unobserved intervals and unavailable counters are explicitly retained.
`make down` and `make docker-down` then passed in 32.435 s and 25.161 s.
Owned containers, networks, volumes and image tags were removed, all nine
unrelated containers stayed unchanged, and both closed histories passed offline
checks. Large raw exports/journals remain ignored; only curated JSON is committed.
`test-production` runs the crash case and then re-verifies all registered recovery
tables on the current volume, including acknowledgements from earlier cases.

The intended default is **2,000,000 seed rows**, **10,000 customers** and
**10,000-row batches**, with two independent Go writers and one reader per engine.
The checked-in [`recovery-contract.json`](recovery-contract.json) declares the
acceptance criteria, client settings and failure boundaries before execution.
Each batch has deterministic values, SHA-256 payloads and explicit ID bounds.
Intents/outcomes are fsynced outside S3; failed or interrupted mutations are
reconciled without blind retries. Both engines must stream and verify every
acknowledged body after recovery against unchanged witnesses and one quiescent
catalog snapshot. Earlier verification exports remain in unique directories.
`make verify-history` re-exports earlier recovery tables on the current volume
after later faults, preserving their original results and writing separate
verification receipts. Cleanup rejects failed/incomplete cases, missing witnesses
and stale verification evidence; do not remove the journals to bypass a failure.
The identified snapshot-ID recorder failure has a separate, narrowly allowlisted
`make retire-failed-case` workflow. Pass `FAILED_CASE`, `SOURCE_MANIFEST` and
`ORIGINAL_MANIFEST` paths to its immutable original seals; optionally pass a
finalized `COMPLETED_VERIFICATION` directory. It rehashes original evidence,
checks container fencing and verifies the same native table with the corrected
image. Its receipt retains `passed: false` and records data preservation
separately. It cannot retire other failed cases or authorize mutation replay.
Later faults still require `make verify-history` before teardown.
Write durations include engine/connection setup, the native insert and connection
close; the preceding intent fsync and following outcome fsync are outside that
timer. Read durations include connection/query setup, fetching, full-body validation
and engine close. Raw durations are nanoseconds; reports must label their units.
Recent versus historical reads describe acknowledged batch age, not proof of a
cold engine or OS cache.
`operation-summary.json` derives per-phase counts/rates and separate successful,
failed and ambiguous request latency distributions from the preserved journals.
It labels crossing-phase requests, pending write intents and percentile sample
counts. Read records are emitted after completion, so an interrupted in-flight
read may have no record. Snapshot-bound native Parquet compression measurement
passed in the corrected live run. It compares uncompressed versus
compressed column-chunk bytes after encoding; CSV size versus allocated storage
is not a compression ratio. Image builds reject HTTPFS, Avro or Iceberg extension
revisions that differ from the recorded pins.

The initial case kills the actual SeaweedFS **mini process** while clients stay
live, holds it down, and restarts the same container/volume. Catalog and S3 fail
together in this deployment; it is neither an independent catalog outage nor an
HA/disk-loss test. The intended active profile is **7 CPUs / 6.5 GiB** in total,
with 1 CPU/1 GiB per writer/reader, 1 CPU/1 GiB storage and 2 CPUs/1.5 GiB query
server. Seed/verification tools use 1 CPU/2 GiB after other clients stop. Native
CPU, memory/peak, disk bytes, I/O and network observations accompany phase-tagged
read/write histories. Failure evidence and native data stay retained until
verification and cleanup guards pass. Separate catalog/network, compactor and
archive/TTL interruption cases remain unrun work in `PLAN.md`.

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
