# LanceDB versioned product retrieval

## Quick start

Run from this directory with Docker Compose v2, Make and Python 3.11+ available. Corpus
preparation downloads 1.16 GB of verified Parquet plus the pinned ONNX model;
encoding is a separate first-run cost. The example owns its cache and verified
inputs under `results/corpus/`; its lifecycle never invokes a sibling example.

1. `make docker-up` — start the local infrastructure and wait for readiness.
2. `make up` — create/index products, or verify the retained catalog without resetting it.
3. `make test` — check complete rows, filters, retrieval and the system-specific behavior.
4. `make down` — verify the witness, then drop the workload table; leave SDK workers running.
5. `make docker-down` — remove owned workers, volume and built images; archive and hash-check evidence.

Default `MODE=cluster`. To run a single-node baseline, complete teardown first,
then use `MODE=single make docker-up`, `MODE=single make up`, and the same
`MODE=single` prefix for subsequent commands. Persistent cluster membership and
storage must not be reused across topology changes.

## Workload and measurements

The common [ESCI fixture](../../datasets/esci/README.md) defaults to **100,000
products**, **1,000 held-out queries**, **384-dimensional** normalized real text
embeddings, **100 synthetic tenants**, and top **10** results. Stock/price/tenant
attributes are deterministic synthetic data. Each engine receives identical
vectors and products. These are product-text retrieval tests, not LLM/RAG tests.

`make benchmark` measures lexical, dense and hybrid searches at **1 and 8
clients**: **16 warmup requests**, then **1,000 timed queries per case**. Set
`BENCH_QUERIES`, `QUALITY_QUERIES` (default 100), `CHECK_QUERIES` (default 50).
Requests use precomputed vectors; tokenization/encoding is excluded. Every timed
response checks complete metadata and versions against a frozen, witness-verified
catalog snapshot, plus dense/hybrid result counts and filter isolation. The timed
boundary includes HTTP, JSON conversion and this validation; lexical searches can
legitimately return fewer results. Exact ANN ranking is evaluated separately through
recall, not asserted equal to exhaustive search. Candidate
nDCG uses graded ESCI judgments with E=1, S=0.1, C=0.01, I=0; zero-IDCG queries
are excluded and counted. Dense recall uses an independent exhaustive NumPy
reference with boundary ties accepted. Quality and ANN recall measure different
things. Lexical tokenization and native hybrid ranking differ between engines.

## Recovery tests

`make test-production` runs separate cases serially, keeping independent Go writer
and reader containers alive before injection, throughout recovery and afterward.
The reader issues point reads and tenant-filtered dense searches directly against
each serving worker, without cross-worker fallback. The writer does not retry a
mutation. Both actors fsync external journals; all earlier histories, including
failed cases and ambiguous outcomes, are retained and reconciled. A new case uses
versions above every earlier attempt. `make up` never resets acknowledged data.

| Command | Operation |
|---|---|
| `make test` | Complete metadata/vector/history checks, 50 dense and 10 filtered queries, snapshot/compaction behavior, then a live correctness window |
| `make writer-crash` | SIGKILL the sole writer, retain the catalog, observe rejected/ambiguous writes and independent reader progress, then restart |
| `make reader-crash` | Cluster only: SIGKILL reader1, observe its read failures alongside surviving read/write service, then reopen its view |
| `make maintenance` | Run native SDK compaction under live updates and searches; quantify backpressure and reconcile afterward |
| `make rolling-restart` | Gracefully stop/start every SDK process serially, checking catalog convergence and new read/write progress after each |
| `make cold-reopen` | Default SIGTERM stop of all workers, require exit 0, retain the catalog, hold 15 seconds and reopen |
| `make crash-reopen` | SIGKILL every worker, require exit 137 without OOM, retain the catalog, hold 15 seconds and reopen |

`make failover` aliases `writer-crash` for compatibility; there is **no writer
election or automatic failover**. `NODE_DOWN_SECONDS=45` controls individual crash
holds (30..300); `UPDATE_BATCH=10` controls rows per SDK merge; `DURATION_SECONDS=1800`
is a safety deadline, not successful completion. Request latency excludes external
witness fsync; complete loop rates include it. The writer waits 100 ms between
batches and the reader 50 ms between iterations. This is a bounded operational
workload, not capacity measurement. Three-second application deadlines can leave
an SDK mutation running; pending requests drain before the final oracle. Native
commit attempts reported by successful merge results are counted separately;
failed outcomes may have unobservable internal attempts.

A fixed catalog version captured while the writer is live must become visible to
every worker before recovery is declared. After stopping writes and draining SDK
mutations, each worker pins the same catalog snapshot and exports **all 100,000
IDs, every metadata/inventory field and a SHA-256 over all 384 stored float32
coordinates per product**. The independent verifier compares those hashes to the
input vectors and bodies to the corpus/witness, checks coverage and duplicates,
and the host audits compressed proof hashes and equality across views. This is
SDK readback of one shared catalog, not independent disk-file decoding or four
durable copies. Latest ACK per product is a lower bound: a later witnessed update
can supersede it. Historical inventory bodies are not an immutable application log.

Reports retain phase-separated successes/errors, request counts/rates, p50/p99,
ambiguous outcomes, extra safe-read transport attempts, catalog versions and
longest successful-completion gaps. Gaps include offered-load think time and
polling; these are not precise failure-detection or election durations. Raw fsynced
journals live under ignored `results/production/<mode>/`. Cleanup archives all
current evidence in `previous-runs/<id>/`, hash-verifies every file, and keeps the
example-owned corpus plus earlier archives. A failed report is never rewritten.

The prepared input is **231,295,436 bytes (220.58 MiB)** across products/queries
JSONL, float32 vectors and exact-reference JSON; product vectors alone are
**153,600,000 bytes (146.48 MiB)**. Seed **20261115**, 100,000 unique IDs, 100 tenants,
1,000 queries, pinned model/revision and every artifact checksum are recorded in
the manifest. Product text includes locale/name/description/brand/color; mutable
inventory fields are integer tenant/stock/price/version. Prepared metadata has
**zero NULLs**, **47,043 empty descriptions**, and **912–1,078 products per tenant**.
The corpus is not a
string-skew or capacity characterization. Input size excludes downloads, duplicate
NumPy files and native data/index/history storage.

No host ports are published. `make cli` opens a shell on the private Compose
network. The HTTP wrapper has no authentication/TLS; read-only worker rejection
is a role guard, not a tenant-security or hostile-client qualification. One local
Docker VM does not model independent hosts/zones.

## Architecture and limitations

LanceDB **0.41.0** is an embedded OSS library. Cluster mode here means **one
writer + three read-only HTTP serving workers** over **one shared local volume**.
It is not a replicated database or distributed consensus deployment. Each worker
has **2 CPUs / 3 GiB**; the writer has the same cap. Single mode queries the writer.
Cluster server caps total **8 CPUs / 12 GiB**; independent Go actors add **4 CPUs /
1 GiB**, and verifier tools **2 CPUs / 3 GiB** (maximum declared live-test budget
**14 CPUs / 16 GiB**, oversubscribing the 11-CPU VM). The separate benchmark uses
servers plus **2 CPUs / 3 GiB** tools, totaling **10 CPUs / 15 GiB** in cluster mode,
**4 CPUs / 6 GiB** in single mode. These are ceilings, not reservations. Corpus
encoding separately has **4 CPUs / 4 GiB** and does not run during measurements.

The maintained Python SDK and Arrow fixed-size lists build an IVF_FLAT cosine
index with 256 partitions, a tenant B-tree, and native FTS. Normal search probes
32 partitions; filtered candidate/tenant searches probe all 256, avoiding short
results at high selectivity. Hybrid search uses the SDK's native fusion. Opening
fresh table handles with zero consistency interval makes version refresh explicit.
The small HTTP wrapper uses the Python standard library; it serializes mutations
at the sole writer and rejects writes on reader endpoints.

The focused test pins an old version, updates a row, checks old/latest views,
compacts while retaining 30 days of history, reopens the old version and restores
the exact pre-test body/version, including acknowledged inventory updates. This
functional snapshot/restore case runs without live writers; the separate maintenance
case exercises compaction under live traffic.

Limits: the shared volume and writer are single failure points; no automatic
writer election, replication or cross-host filesystem guarantees are claimed.
Independent workers may improve serving concurrency but share CPU, filesystem
and page cache. Version history consumes disk until retention cleanup; freshly
updated rows can remain unindexed and dense searches include the SDK's normal
unindexed-tail path. Fast-search skipping of that tail is deliberately disabled.
The live workload changes inventory metadata and carries the original vectors;
model re-embedding and FTS freshness after updates are not evaluated.
These HTTP timings include the wrapper; they are not direct embedded SDK timings.

[Embedded documentation](https://docs.lancedb.com/_llms/embedded.txt),
[inspected SDK query implementation](https://github.com/lancedb/lancedb/blob/v0.41.0/python/python/lancedb/query.py),
[version/maintenance implementation](https://github.com/lancedb/lancedb/blob/v0.41.0/python/python/lancedb/table.py).

## Verified recovery backfill (2026-11-19)

The cluster's complete eight-case serial suite passed, followed by a stronger live
maintenance case proving native compaction work. **Nine preserved histories**
reconcile **16,760 acknowledged / 24,250 attempted inventory rows**, **7,490
ambiguous outcomes**, **zero acknowledged-version loss**, **zero corrupt bodies or
vectors** and **zero application mutation retries**. All four SDK views agreed
across 100,000 complete products and 384 stored float32 values per product. These
are four reads of **one shared durable catalog**, not four replicated copies.

| Case | ACK / attempted rows | Ambiguous rows | Longest ACK / GET / search gap (s) |
|---|---:|---:|---:|
| correctness | 2,310 / 2,310 | 0 | 0.13 / 0.18 / 0.18 |
| writer-crash | 990 / 5,370 | 4,380 | 47.18 / 0.21 / 0.21 |
| reader-crash | 4,350 / 4,350 | 0 | 0.17 / 0.32 / 0.36 |
| maintenance | 1,080 / 1,080 | 0 | 2.49 / 0.26 / 0.25 |
| rolling-restart | 2,260 / 2,360 | 100 | 2.17 / 0.20 / 0.19 |
| cold-reopen | 1,440 / 2,950 | 1,510 | 16.79 / 23.10 / 23.21 |
| crash-reopen | 1,320 / 2,810 | 1,490 | 16.70 / 22.44 / 22.49 |
| correctness | 1,870 / 1,870 | 0 | 0.17 / 0.29 / 0.29 |
| maintenance (fragment proof) | 1,140 / 1,150 | 10 | 4.29 / 0.33 / 0.27 |

Individual crashes held the actual process down for **45 seconds**. The sole-writer
outage recorded **zero read errors** while writes stopped; killing reader1 recorded
**279 read errors** while surviving workers and writes progressed. Rolling restart
recorded **66** read errors. Whole-stack graceful/SIGKILL holds were **15 seconds**,
with **684 / 568 read errors**, respectively. Default SIGTERM shutdown proved exit
**0** for all workers; forced shutdown proved exit **137**, without OOM. Snapshots,
container identities, survivor stability, retained-volume identities, catalog
convergence and post-recovery progress are saved independently.

The synchronous SDK `optimize()` returns `None`; the test uses native counters,
not that return value, as evidence of completed work. Native maintenance reduced
**795 → 10 fragments**, **16,060 → 80 deleted rows**
in the current manifest, and **7,930 → 80 unindexed rows** while traffic stayed
live. Subsequent successful writes naturally created another tail: the final
quiescent manifest had **67 fragments**. Native plans for the old/current snapshots
both show IVF index lookup plus a scan of unindexed rows. Snapshot history/data
retained for 30 days is additional storage; current-manifest bytes are not the
whole catalog directory size.

The final oracle found **10 visible non-ACK inventory versions** in every view.
They match fsynced intents and remain ambiguous, **not acknowledged**: a request
can time out while queued behind compaction and still commit. Other ambiguous
attempts were absent at the final snapshot; these unique-key runs do not establish
historical outcomes after arbitrary overwrites. Do not blindly retry a mutation.
Host proofs independently enforce current ACK lower bounds and quantify visible
non-ACK versions; raw histories preserve the original responses.

The first pre-injection maintenance follow-up stopped because host free space
fell below **4 GiB**. Approved unused build-cache cleanup restored headroom;
verified image/source hashes allowed reuse of the already-built images with
`make -o build maintenance benchmark up down docker-down`. No database reset or
vendor-image/foreign-volume deletion was used. Retained `make up`, full teardown
and cleanup passed; **625 evidence files** were hash-verified in archive
`results/production/cluster/previous-runs/ca35653eb0bd492cbbcd51f6aa649fbd/`.
The separate final ambiguity audit under `results/analysis/` references its
archive-proof hash and does not modify archived evidence.

### Post-maintenance warm diagnostics

Apple M4 Pro, Docker **29.5.3**, aarch64 VM **11 CPUs / 26,216,296,448 bytes RAM**.
Each case uses **16 warmups + 1,000 timed requests**; quality uses **100 queries**.
**6,000 timed requests / zero failures**; complete bodies, versions, dense/hybrid
counts and filters were checked on **63,651 returned hits** including warmups and
quality checks. Dense recall@10 was **1.0000**. The quiescent expected-body snapshot
contains all **100,000 products** and is SHA-256 identified in the result.

| Query / clients | QPS | p50 / p99 (ms) |
|---|---:|---:|
| lexical/clients-1 | 57.71 | 16.59 / 27.47 |
| lexical/clients-8 | 221.31 | 33.65 / 70.82 |
| dense/clients-1 | 70.21 | 13.71 / 19.20 |
| dense/clients-8 | 244.73 | 28.46 / 61.02 |
| hybrid/clients-1 | 42.69 | 23.11 / 29.00 |
| hybrid/clients-8 | 116.50 | 45.23 / 193.70 |

The sampler retained **17 all-server CPU/memory samples / zero sampling errors**.
These are sampled utilization/cache-adjusted memory, not integrated CPU time or
peak RSS. Budgets are stated above. This is **one warm diagnostic trial**, not a
three-trial, cold-cache or matched-resource comparison with another engine.
The timed boundary includes HTTP, JSON and complete body/version validation.

Before the final maintenance, the updated catalog had **751 fragments / 7,490
unindexed rows**. Its earlier ID-only warm diagnostic recorded one-client
**9.88 lexical / 14.20 dense / 6.97 hybrid QPS**, with **93 resource samples** and no
sampling/query errors. That retained run uses a different response-validation
boundary and catalog state; do not present a controlled speedup ratio or pool the
runs. The observation illustrates why update-tail and maintenance state must be
reported with search numbers. Each inventory update rewrites the full product,
including its original vector/text; PostgreSQL's example instead updates a
separate inventory table. Neither path is a normalized mutation benchmark.

**Seven host controls**, **nine native SDK controls**, and **six Go race-tested
controls** cover stale images, unknown fencing, wrong/short/stale HTTP bodies,
actual stored-vector corruption, conditional version updates, snapshot restore,
fixed live catalog fences and ambiguous/truncated histories. Docker builds run
native SDK controls plus Go tests/vet. No failed or ambiguous response is relabeled
an acknowledgement.

### Single-node qualification

The seven applicable serial cases plus a final live maintenance case passed:
**eight histories**, **10,540 ACK / 17,230 attempted rows**, **6,690 ambiguous**,
zero acknowledged-version loss/corruption/retries. Each full proof checked all
**100,000 bodies / 38,400,000 stored float32 coordinates**. Final reconciliation
also found **10 visible, witnessed non-ACK versions**, without relabeling them ACKs.

| Case | ACK / attempted rows | Ambiguous rows | Longest ACK / GET / search gap (s) |
|---|---:|---:|---:|
| correctness | 2,280 / 2,280 | 0 | 0.14 / 0.24 / 0.24 |
| writer-crash | 950 / 5,180 | 4,230 | 47.10 / 47.01 / 47.13 |
| maintenance | 1,170 / 1,170 | 0 | 2.09 / 0.22 / 0.23 |
| rolling-restart | 1,160 / 1,190 | 30 | 1.45 / 1.48 / 1.51 |
| cold-reopen | 980 / 2,190 | 1,210 | 16.67 / 16.65 / 16.81 |
| crash-reopen | 920 / 2,130 | 1,210 | 16.52 / 16.44 / 16.52 |
| correctness | 1,960 / 1,960 | 0 | 0.19 / 0.27 / 0.29 |
| maintenance | 1,120 / 1,130 | 10 | 4.29 / 0.24 / 0.31 |

The single-node benchmark validated **63,651 returned hits** against 100,000 frozen
current bodies, with **6,000 timed requests / zero failures**, dense recall@10
**1.0000**, **22 resource samples / zero sampling errors**. Warmups, input, quality
checks and timing boundaries match the new cluster diagnostic; total server
budgets differ, and neither profile is a replicated storage system.

| Query / clients | Single QPS | p50 / p99 (ms) |
|---|---:|---:|
| lexical/clients-1 | 72.19 | 13.19 / 26.95 |
| lexical/clients-8 | 105.78 | 76.98 / 112.61 |
| dense/clients-1 | 83.20 | 11.56 / 15.97 |
| dense/clients-8 | 104.36 | 83.05 / 103.68 |
| hybrid/clients-1 | 50.90 | 19.37 / 25.94 |
| hybrid/clients-8 | 49.29 | 166.00 / 264.53 |

Cleanup hash-verified **493 files** in single archive
`29a52f741fd84aea9149d9042ea17e58`. A second fresh five-command lifecycle passed
another **2,270 ACKs / zero ambiguous outcomes**, followed by an actual retained
`make up` in a **separate Make invocation** and complete native/body verification.
Its cleanup hash-verified **74 files** in archive
`58d70ed421d642ab85b5bf43aa6bcf24`. Make executes a target only once per invocation:
`make up test up` does not test setup twice. All previous archives/corpus survive
teardown. The final cleanup preserved all 23 pre-existing volumes and removed
only this example's containers, network, catalog volume and locally built tags.

## Historical benchmark (2026-11-15)

These predate the independent-reader and preserved-history backfill. The original
coupled loop does not qualify continuous read availability during blocked writes.

Measured **2026-11-15, Apple M4 Pro, Docker VM aarch64 (11 CPUs / 24.42 GiB)**.
Server caps are listed above; the load/benchmark client has **2 CPUs / 3 GiB**.
One warm local run per case, one system active at a time; no emulation. Raw
per-request samples, fixture hashes, resource caps and periodic Docker resource
samples are in `results/<mode>/`. Sampled usage is not an exact peak RSS.
No confidence intervals, cold-cache, WAN or independent-host results are claimed.
Native response projections and ranking algorithms differ between engines, so
cross-engine QPS is not a payload-normalized leaderboard. Single/cluster cases
within an engine use identical request/response shapes. Setup timings include
native index construction. ANN search effort and recall differ between engines;
compare throughput at the reported recall rather than treating native settings
as equivalent. Write recovery starts at the first failed new
attempt after the fault marker; maximum ACK gap also includes in-flight stalls.

### Single

100,000 products / 1,000 held-out queries; 100,000 rows matched the full-value oracle.
Observed setup load/index time: **10.33 s** (**9680.00 rows/s**).

| Mode | Clients | QPS | p50 ms | p95 ms | p99 ms | Dense recall@10 |
|---|---:|---:|---:|---:|---:|---:|
| lexical | 1 | 230.45 | 3.92 | 4.65 | 6.00 | — |
| lexical | 8 | 349.32 | 19.06 | 40.20 | 45.52 | — |
| dense | 1 | 193.17 | 4.98 | 5.60 | 6.48 | 0.9519 |
| dense | 8 | 285.27 | 21.69 | 49.86 | 54.54 | 0.9519 |
| hybrid | 1 | 152.55 | 6.49 | 7.14 | 7.67 | — |
| hybrid | 8 | 142.48 | 57.60 | 75.21 | 80.33 | — |

| Candidate ranking | nDCG@10 | Scorable / zero-IDCG queries |
|---|---:|---:|
| lexical | 0.5815 | 100 / 0 |
| dense | 0.5702 | 100 / 0 |
| hybrid | 0.5836 | 100 / 0 |

Correctness sample: 50 dense queries, mean recall@10 0.9740; 10 tenant-filtered queries, mean eligible-set recall@10 1.0000. No filter leaks or short dense/eligible results.

### Cluster

100,000 products / 1,000 held-out queries; 100,000 rows matched the full-value oracle.
Observed setup load/index time: **11.49 s** (**8704.46 rows/s**).

| Mode | Clients | QPS | p50 ms | p95 ms | p99 ms | Dense recall@10 |
|---|---:|---:|---:|---:|---:|---:|
| lexical | 1 | 186.27 | 4.94 | 5.93 | 6.96 | — |
| lexical | 8 | 880.87 | 8.49 | 11.23 | 18.90 | — |
| dense | 1 | 161.09 | 5.96 | 6.69 | 7.61 | 0.9490 |
| dense | 8 | 734.25 | 10.30 | 13.51 | 15.57 | 0.9490 |
| hybrid | 1 | 130.63 | 7.59 | 8.31 | 9.07 | — |
| hybrid | 8 | 397.80 | 14.95 | 38.62 | 43.57 | — |

| Candidate ranking | nDCG@10 | Scorable / zero-IDCG queries |
|---|---:|---:|
| lexical | 0.5815 | 100 / 0 |
| dense | 0.5702 | 100 / 0 |
| hybrid | 0.5836 | 100 / 0 |

Correctness sample: 50 dense queries, mean recall@10 0.9840; 10 tenant-filtered queries, mean eligible-set recall@10 1.0000. No filter leaks or short dense/eligible results.

| Crash check | Observed |
|---|---:|
| Killed process | writer |
| Acknowledged updates | 2990 |
| Acknowledged unique IDs | 2990 |
| Missing acknowledged versions | 0 |
| Successful searches while absent | 234 |
| Failed searches while absent | 0 |
| New update batches ACKed while absent | 0 |
| Failed-attempt to resumed-write ACK | 31.32 s |
| Largest ACK gap | 31.67 s |
| Restart to infrastructure health | 5.74 s |
| Ambiguous/failed update batches | 246 |

The historical infrastructure-health timing did not establish full vector coverage
or independent replica catch-up. Current SDK proofs supersede the old metadata-only
oracle. No host/disk durability guarantee is implied.


### Separate reader failure

The retired historical command `FAULT_SERVICE=reader1 make failover`, run from
a reset 100,000-row catalog, killed
only reader1. The writer, reader2 and reader3 retained their container IDs and
start times. While reader1 was absent, **218 update batches** and
**217 searches** succeeded, with **0 failed mutation batches**,
**0 failed searches**, and **0 missing acknowledged versions**.
The independent raw journal is in `results/cluster/reader-loss/`; the writer
isolation rerun is in `results/cluster/writer-isolation/`. Those older cases reset
the fixture between faults and do not establish cross-run history preservation.
Use the current independent-reader suite above for recovery qualification.

Version-test compaction took **1.633 s** while retaining the pinned version.
The test also proved the updated row remained visible through the unindexed
vector tail and that a read-only worker rejected mutation.

## Directory structure

```text
Makefile, docker-compose.yml, control.py, test_controls.py, view_proofs.py
corpus.py, prepare_build.py
app/{Dockerfile,requirements.txt,client.py,server.py,production.py,test_native.py}
inventory-workload/
  Dockerfile, go.mod
  main.go, http.go, writer.go, reader.go, history.go, journal.go
  http_test.go, history_test.go
  fixture.go -> ../../../typesense/product-search/inventory-workload/fixture.go
retrieval-corpus -> ../../datasets/esci
.build/                        # ignored materialized regular-file contexts
results/corpus/                # owned verified inputs/cache, retained
results/production/<mode>/     # journals, native exports, reports and archives
```

Only tracked source is linked. `prepare_build.py` materializes regular files using
hard links where supported and copies otherwise before the local Docker build;
materialized files are never committed. For this run, the identical verified prepared corpus was copied into this
example's cache from the freshly encoded pgvector fixture, with no symlinks or
hard links to generated files. `cache-import.json` records that provenance and
checksums. LanceDB encoding was not separately rerun. A clean checkout can
regenerate its own artifacts through this example's pinned corpus Dockerfile and
`make docker-up`; runtime never requires a sibling example's generated state.

Pinned SDK implementation references: [conditional merge and result counters](https://github.com/lancedb/lancedb/blob/v0.41.0/python/python/lancedb/merge.py),
[read consistency, snapshot/restore and maintenance](https://github.com/lancedb/lancedb/blob/v0.41.0/python/python/lancedb/table.py),
[database ownership and close](https://github.com/lancedb/lancedb/blob/v0.41.0/python/python/lancedb/db.py).

Extended network/volume-I/O failures, pressure, SDK-version upgrades, independent
backup restore, multi-host filesystem semantics and fair cold/multi-trial capacity
comparisons remain unrun. Replica quorum, replica repair and native shard routing
are unsupported in this embedded shared-catalog deployment; serving workers are
not a substitute for database replication. Losing the catalog volume requires an
independent backup, not rebuilding it from a surviving worker.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
