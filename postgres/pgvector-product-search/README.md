# pgvector inventory-aware retrieval

## Quick start

Run from this directory with Docker Compose v2, Make and Python 3.11+ available. Corpus
preparation downloads 1.16 GB of verified Parquet plus the pinned ONNX model;
encoding is a separate first-run cost. This example prepares and verifies its own
corpus under `results/corpus/`; a sibling example is not required.

1. `make docker-up` — start the local infrastructure and wait for readiness.
2. `make up` — verify/load the example-owned corpus, or verify retained data without resetting it.
3. `make test` — check complete rows, filters, retrieval and the system-specific behavior.
4. `make down` — remove the workload tables, keeping PostgreSQL/etcd running.
5. `make docker-down` — remove this project, volumes and locally built images; archive and hash-check evidence.

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

The prepared retrieval input contains **231,295,436 bytes (220.58 MiB)** across
products/queries JSONL, product/query float32 vectors and exact-reference JSON.
Product vectors alone are **153,600,000 bytes (146.48 MiB)**. Seed **20261115**,
100,000 distinct product IDs, 100 deterministic tenants and 1,000 queries are
recorded with per-file SHA-256 hashes in the manifest. Text fields include source
ID, locale, name, description, brand and color; mutable integer fields are tenant,
stock, price in cents and version. Full vector SHA-256 is
`de5d75f052a2449a282e8683088caf3d8d232a5fc1d920ac7e902b6a6e9f64a3`.
Input bytes exclude cache/downloads, duplicate NumPy files and database/index/WAL
storage. ESCI product/tenant distributions are not a general skew benchmark.

`make benchmark` measures lexical, dense and hybrid searches at **1 and 8
clients**: **16 warmup requests**, then **1,000 timed queries per case**. Set
`BENCH_QUERIES`, `QUALITY_QUERIES` (default 100), `CHECK_QUERIES` (default 50).
Requests use precomputed vectors; tokenization/encoding is excluded. Candidate
nDCG uses graded ESCI judgments with E=1, S=0.1, C=0.01, I=0; zero-IDCG queries
are excluded and counted. Dense recall uses an independent exhaustive NumPy
reference with boundary ties accepted. Quality and ANN recall measure different
things. Lexical tokenization and native hybrid ranking differ between engines.

## Recovery tests

`make test-production` runs the applicable cases **serially**, with independent Go
writer and reader containers declared in this example's Compose file. Each case
starts both actors before injection, requires fresh progress while degraded where
available, waits for native recovery, stops writes and keeps reads running during
full-copy reconciliation. It never reloads products between cases.

| Command | Operation |
|---|---|
| `make test` | Full-copy/history verification, 50 dense/10 tenant-filtered recall checks, transaction rollback, then live correctness window |
| `make failover` | Discover and SIGKILL the actual primary; require new-primary writes while the old owner remains fenced |
| `make replica-crash` | Discover and SIGKILL the actual synchronous standby; require degraded read/write progress |
| `make disk-failure` | Fence a replica, remove only its label-verified owned volume, prove the replacement empty, then let Patroni clone it |
| `make rebuilt-crash` | After a qualified disk replacement, fence a different peer and directly verify every rebuilt row/vector while it remains absent |
| `make cold-reopen` | Stop all PostgreSQL containers with retained volumes, hold for 15 seconds, restart and reconcile |
| `make crash-reopen` | SIGKILL all PostgreSQL containers, retain volumes, hold for 15 seconds, restart and reconcile |

If a disk controller fails after the old owner/volume has been removed, preserve
the failed case and use `DISK_CASE=<case-id> make resume-disk-recovery`. It requires
the recorded failed disk case, positive old-owner absence and an owned, empty
replacement; it never deletes an existing replacement or rewrites the failed
report. This is administrative healing without live actors, not a passed recovery
test. Run a fresh complete fault case afterward to qualify availability under load.

Single mode runs correctness and both complete-stack reopen cases; replica/disk
cases require cluster mode. `NODE_DOWN_SECONDS=45` controls single-member hold time
(minimum 30); `UPDATE_BATCH=10` controls transactional inventory batch size;
`DURATION_SECONDS=1800` is an actor safety deadline, not a successful completion.
The writer pauses 100 ms between batches and the reader 50 ms between iterations.
These are bounded recovery observations, **not capacity benchmarks**. No mutation
is retried. Three-second operation deadlines include primary discovery, pool use
and SQL/commit, excluding witness fsync. A timed-out commit can still apply;
its intent remains eligible in reconciliation but never becomes an ACK merely
because its body is later visible.

The external fsynced witness records complete attempted and acknowledged inventory
bodies/versions. Every case starts above all earlier attempted versions and checks
all earlier histories, including failed cases. For this mutable inventory, the
latest ACK per product is a lower bound; a later witnessed update legitimately
supersedes earlier values. This does not retain an immutable copy of every old
inventory version. Readers independently issue primary
point reads and tenant-filtered dense searches, check witnessed bodies and reject
versions older than ACKs known before their query. Search recall is measured
separately; live fault searches check body/tenant/count correctness. Read and write
successes/errors, counts, rates and p50/p99 are reported separately by completion
phase. Gaps include offered-load think time; they are not exact native election
or outage durations. Raw events preserve the timing boundaries.

A replica is recovered only after PostgreSQL reports streaming membership on the
correct timeline and **both primary-reported replay and standby replay LSNs** reach
a post-write flush fence. All copies are then scanned directly in native SQL:
**every ID, metadata field, inventory body/version and all 384 float32 bit patterns**
must match the input/witness. Compressed per-row proofs retain vector hashes and
complete metadata. This is native SQL readback, not independent decoding of
PostgreSQL data files. The rebuilt-copy check also reads all data on the rebuilt
member while a different peer is fenced. Container identities, postmaster start
times, volume identities, durability settings and unrelated survivors are checked.

`make up` binds the database to the exact corpus manifest. Partial/legacy state,
a different corpus, or an empty database with existing histories fail closed.
Diagnose and preserve a failed run before explicitly using the full teardown.
`docker-down` moves current evidence into `previous-runs/<id>/` and verifies every
file hash; it retains all previous archives and the example-owned corpus. It does
not qualify earlier failed reports as passes. New lifecycles begin new datasets.

No host ports are published. Credentials are isolated development constants, with
only basic wrong-password and native read-only replica rejection checks. TLS and
least-privilege role isolation remain unqualified. All replicas share one Docker VM; this does
not establish host/zone fault independence.

## Architecture and limitations

PostgreSQL **17** / pgvector **0.8.7** stores product text/vectors and a separate
transactional inventory table. Queries join by product ID; tenant filters use
current inventory. HNSW cosine M=16/ef_construction=128, ef_search=256,
iterative_scan=strict_order and max_scan_tuples=50,000. A GIN text index supplies
lexical retrieval; hybrid SQL combines 100 candidates from each path using RRF60.
The focused test joins tenant, stock and a 50,100-cent budget, sets one returned
product out of stock inside a transaction, verifies exclusion, rolls back and
verifies restored eligibility. It also captures EXPLAIN ANALYZE/BUFFERS.

Cluster mode has **1 primary + 2 physical replicas**, managed by Patroni 4.0.6
and **3 etcd 3.5.21** members. Strict synchronous mode requires one synchronous
replica, with timeline checks enabled; reads use the discovered primary. Each PostgreSQL node: **2 CPUs /
2 GiB**; each etcd: **0.5 CPU / 256 MiB**. Single mode runs PostgreSQL directly with data checksums enabled.
The Go writer and reader each have **2 CPUs / 512 MiB**; the verification tools
have **2 CPUs / 3 GiB**. Cluster server ceilings total **7.5 CPUs / 6.75 GiB**,
or **13.5 CPUs / 10.75 GiB** including both actors and verification tools. These
are container limits, not reserved physical cores. Corpus encoding separately
uses **4 CPUs / 4 GiB** and is excluded from recovery/performance claims.
Psycopg binary COPY and bounded pools use maintained pgvector type adapters.
Each PostgreSQL container has **1 GiB /dev/shm**: the full parallel HNSW build
requested about 509 MiB and failed with Docker's default 64 MiB shared-memory
mount (`could not resize shared memory segment`). This is separate from disk
free space and stays within the 2 GiB container memory cap.

Limits: physical replication does not shard the catalog, and this client does
not load-balance reads across replicas. Strict sync can block writes rather than
risk an unreplicated ACK. Arbitrary joins/selective filters can change plans and
ANN recall; iterative scans are bounded. Candidate reranking may choose an exact
scan. EXPLAIN output records what the planner actually selected. Network partitions/quorum rejection, native scaling, rolling upgrades, resource
pressure, injected EIO/corruption and independent backup restore remain unrun.
Complete-stack SIGKILL differs from physical power loss or a failed storage device.
The empty-volume experiment establishes native rebuild, not detection of real
hardware corruption. One synchronous standby protects the configured contract,
not arbitrary simultaneous failures. Multi-host durability is outside this lab.

[pgvector docs/source](https://github.com/pgvector/pgvector/tree/10a73eceefe033ac944f91782c553b5fc67eccb9),
[inspected iterative HNSW scan](https://github.com/pgvector/pgvector/blob/10a73eceefe033ac944f91782c553b5fc67eccb9/src/hnswscan.c),
[Patroni replication modes](https://patroni.readthedocs.io/en/latest/replication_modes.html).

## Verified recovery backfill (2026-11-19)

A fresh `make docker-up up test-production benchmark` passed all **eight** cluster
cases. Each final proof scanned all **100,000 products** on each of **three native
copies**, including **115,200,000 float32 coordinates**. Totals: **27,870 ACK /
33,860 attempted inventory updates**, **5,990 ambiguous**, **zero acknowledged-version
loss**, **zero corrupt bodies/vectors**, **zero mutation retries**. Counts below
are individual rows; each transactional request contains 10 rows.

| Case | ACK / attempted rows | Ambiguous rows | Longest ACK / GET / search gap (s) |
|---|---:|---:|---:|
| correctness | 2,530 / 2,530 | 0 | 0.22 / 0.13 / 0.13 |
| primary-crash | 4,000 / 5,640 | 1,640 | 18.57 / 18.37 / 18.60 |
| replica-crash | 5,440 / 5,450 | 10 | 3.22 / 0.12 / 0.12 |
| disk-failure | 6,130 / 6,130 | 0 | 0.34 / 0.13 / 0.12 |
| rebuilt-crash | 4,740 / 6,360 | 1,620 | 18.15 / 18.03 / 18.20 |
| cold-reopen | 1,260 / 2,620 | 1,360 | 19.36 / 19.06 / 19.17 |
| crash-reopen | 1,210 / 2,570 | 1,360 | 21.66 / 16.73 / 16.82 |
| correctness | 2,560 / 2,560 | 0 | 0.11 / 0.12 / 0.11 |

Individual faults held the selected process down for **45 seconds**; whole-stack
cases held every PostgreSQL process down for **15 seconds** after confirmed exit.
The graceful case exercised the Patroni image default **SIGTERM**: all exit **0**.
Forced cases proved exit **137**, without OOM. Patroni cloned the empty replacement;
the rebuilt member then served complete data while a different peer was fenced.
Native streaming/timeline checks and a fixed, captured WAL fence were required
**while writes remained live**, followed by a quiescent full-copy audit.

The single-node profile passed **five histories**: its four-case serial suite plus
a default-signal graceful reopen. It recorded **8,720 ACK / 12,900 attempts**,
**4,180 ambiguous**, zero acknowledged-version loss and **38,400,000 vector
coordinates** checked per complete proof. Direct PostgreSQL uses its native
**SIGINT** shutdown signal; Patroni's PID 1 needs **SIGTERM**.

Earlier development evidence remains intact in a separate cluster archive:
**28 histories**, **24 passed / 4 failed**, **108,320 ACK / 127,590 attempts**,
**19,270 ambiguous**, zero acknowledged-version loss at the final audit. Failed
cases exposed an unsupported Compose `create --no-deps` flag, inherited Postgres
`SIGINT` shutdown under Patroni, and a false comparison of unordered Docker mount
arrays. Fixes were followed by fresh cases. Administrative empty-disk healing was
recorded separately and did not turn the failed disk test into a pass. Initial
setup also exposed an incorrect `np.asarray(pgvector.Vector)` conversion, fixed
through the public `to_numpy()` adapter and a negative control. An initial
benchmark-wrapper configuration failure happened before queries and is preserved.

Raw evidence includes fsynced journals, success/error latency by phase, native
barriers and process/volume identities, and compressed full-row proofs. Host
checks verify hashes, coverage and exact equality of quiescent native copies.
**16 host controls**, **7 native control groups**, and **3 Go race-tested negative
controls** cover false-success paths. Cleanup hash-verified **1,414 earlier-cluster**,
**342 single-node**, and **565 fresh-cluster** files, preserving all **23**
pre-existing volumes. Archives live under `results/production/<mode>/previous-runs/`:
earlier cluster `2f994882c0b64ecfbdbf31a75df2c7e9`, single
`46b9821a9c8a4296bc4518523a450578`, fresh cluster
`02ac15c0d98f49cca530fa6db7b6dcb0`. Failed reports remain failed.

### Warm diagnostic queries after recovery

Measured on **Apple M4 Pro / aarch64**, Docker **29.5.3**, VM **11 CPUs /
26,216,296,448 bytes RAM**. Each cell is **one warm trial of 1,000 requests**,
after **16 warmups**; quality uses **100 queries**. No request failures. Single
and fresh-cluster runs were separate, with only the measured system active.
This cluster is physical replication, not sharded or read-scaled search.

| Query / concurrency | Fresh cluster QPS | p50 / p99 (ms) | Single QPS | p50 / p99 (ms) |
|---|---:|---:|---:|---:|
| dense/clients-1 | 170.99 | 5.37 / 11.20 | 225.35 | 4.13 / 7.28 |
| dense/clients-8 | 248.95 | 10.83 / 88.81 | 267.58 | 9.45 / 88.28 |
| lexical/clients-1 | 1739.19 | 0.44 / 2.44 | 1904.36 | 0.41 / 2.13 |
| lexical/clients-8 | 1782.90 | 4.15 / 12.63 | 1904.79 | 4.01 / 8.16 |
| hybrid/clients-1 | 147.69 | 6.55 / 11.64 | 189.31 | 5.09 / 9.70 |
| hybrid/clients-8 | 216.83 | 12.13 / 97.46 | 214.51 | 11.61 / 93.59 |

Docker CPU/memory samples cover every PostgreSQL/etcd server; sampling cadence and
errors are retained. These are sampled utilization and cache-adjusted memory,
**not integrated CPU core-seconds or peak RSS**. Server and tools limits are stated
above. Encoding/setup are excluded. These diagnostics do **not** meet the three
fresh-trial, cold-cache and matched-budget requirements for a fair comparison or
winner claim. The earlier retained-fixture benchmark remains separately archived.

## Historical benchmark (2026-11-15)

These measurements precede the independent-reader/history backfill. The original
coupled churn loop cannot establish continuous read availability; its crash
counts below remain historical, not qualification of the new production suite.

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
Observed setup load/index time: **37.74 s** (**2649.57 rows/s**).

| Mode | Clients | QPS | p50 ms | p95 ms | p99 ms | Dense recall@10 |
|---|---:|---:|---:|---:|---:|---:|
| dense | 1 | 274.03 | 3.45 | 4.88 | 5.58 | 0.9915 |
| dense | 8 | 291.45 | 8.64 | 83.23 | 89.40 | 0.9915 |
| lexical | 1 | 1784.11 | 0.38 | 0.99 | 4.11 | — |
| lexical | 8 | 1917.90 | 3.94 | 6.82 | 9.77 | — |
| hybrid | 1 | 205.41 | 4.65 | 6.74 | 9.78 | — |
| hybrid | 8 | 224.69 | 10.77 | 87.91 | 126.28 | — |

| Candidate ranking | nDCG@10 | Scorable / zero-IDCG queries |
|---|---:|---:|
| dense | 0.5702 | 100 / 0 |
| lexical | 0.1975 | 100 / 0 |
| hybrid | 0.5858 | 100 / 0 |

Correctness sample: 50 dense queries, mean recall@10 0.9980; 10 tenant-filtered queries, mean eligible-set recall@10 0.9400. No filter leaks or short dense/eligible results.

### Cluster

100,000 products / 1,000 held-out queries; 100,000 rows matched the full-value oracle.
Observed setup load/index time: **39.06 s** (**2559.89 rows/s**).

| Mode | Clients | QPS | p50 ms | p95 ms | p99 ms | Dense recall@10 |
|---|---:|---:|---:|---:|---:|---:|
| dense | 1 | 255.69 | 3.63 | 5.37 | 6.26 | 0.9919 |
| dense | 8 | 290.82 | 8.59 | 83.47 | 86.29 | 0.9919 |
| lexical | 1 | 1748.41 | 0.38 | 1.06 | 4.31 | — |
| lexical | 8 | 1971.20 | 3.79 | 7.26 | 9.78 | — |
| hybrid | 1 | 194.19 | 4.88 | 7.22 | 10.60 | — |
| hybrid | 8 | 220.36 | 11.46 | 87.57 | 93.22 | — |

| Candidate ranking | nDCG@10 | Scorable / zero-IDCG queries |
|---|---:|---:|
| dense | 0.5702 | 100 / 0 |
| lexical | 0.1975 | 100 / 0 |
| hybrid | 0.5858 | 100 / 0 |

Correctness sample: 50 dense queries, mean recall@10 0.9980; 10 tenant-filtered queries, mean eligible-set recall@10 0.9400. No filter leaks or short dense/eligible results.

| Crash check | Observed |
|---|---:|
| Killed process | pg0 |
| Acknowledged updates | 4480 |
| Acknowledged unique IDs | 4480 |
| Missing acknowledged versions | 0 |
| Successful searches while absent | 81 |
| Failed searches while absent | 121 |
| New update batches ACKed while absent | 79 |
| Failed-attempt to resumed-write ACK | 20.61 s |
| Largest ACK gap | 20.72 s |
| Restart to infrastructure health | 6.22 s |
| Ambiguous/failed update batches | 122 |

Infrastructure health is not replica catch-up time: the separate full reconciliation waited for all required copies/readers. No end-to-end host/disk failure guarantee is implied.


## Directory structure

```text
Makefile, docker-compose.yml, control.py
corpus.py, prepare_build.py, replica_proofs.py
test_controls.py, test_replica_proofs.py
patroni/{Dockerfile,patroni.yml}
app/{Dockerfile,requirements.txt,client.py,production.py,test_native.py}
inventory-workload/
  Dockerfile, go.mod, go.sum
  main.go, writer.go, reader.go, history.go, history_test.go
  fixture.go -> ../../../typesense/product-search/inventory-workload/fixture.go
  journal.go -> ../../../typesense/product-search/inventory-workload/journal.go
retrieval-corpus -> ../../datasets/esci
.build/                         # ignored regular-file Docker contexts
results/corpus/                 # owned, verified inputs/cache; retained
results/production/<mode>/       # cases, native proofs and previous-run archives
```

Only tracked source is linked. `prepare_build.py` materializes these links as
hard links where supported, copies otherwise, into ignored `.build/` contexts.
It never imports another example's generated corpus or invokes its Makefile.
`make check-controls` exercises host fencing/report guards; the pinned tools image
runs `python -m unittest test_native` for native-barrier/witness/vector negative
controls. Docker builds run Go tests and vet; host `go test -race ./...` also
checks the independent workload's history tail and stale/wrong-body rejection.

Pinned implementation references:
[Patroni 4.0.6 replication modes](https://github.com/patroni/patroni/blob/v4.0.6/docs/replication_modes.rst),
[Patroni 4.0.6 HA controller](https://github.com/patroni/patroni/blob/v4.0.6/patroni/ha.py),
[PostgreSQL 17 replication](https://www.postgresql.org/docs/17/warm-standby.html),
[pgvector 0.8.7 vector serialization](https://github.com/pgvector/pgvector/blob/v0.8.7/src/vector.c),
[pgx 5.7.6 transaction handling](https://github.com/jackc/pgx/blob/v5.7.6/tx.go).
