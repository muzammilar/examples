# pgvector inventory-aware retrieval

## Quick start

Run from this directory with Docker Compose v2, Make and Python 3.11+ available. Corpus
preparation downloads 1.16 GB of verified Parquet plus the pinned ONNX model;
encoding is a separate first-run cost shared by all four examples.

1. `make docker-up` — start the local infrastructure and wait for readiness.
2. `make up` — prepare the shared corpus, build the client and load/index products.
3. `make test` — check complete rows, filters, retrieval and the system-specific behavior.
4. `make down` — stop/remove the example services, preserving owned persistent storage.
5. `make docker-down` — remove this Compose project, volumes and its locally built images.

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
Requests use precomputed vectors; tokenization/encoding is excluded. Candidate
nDCG uses graded ESCI judgments with E=1, S=0.1, C=0.01, I=0; zero-IDCG queries
are excluded and counted. Dense recall uses an independent exhaustive NumPy
reference with boundary ties accepted. Quality and ANN recall measure different
things. Lexical tokenization and native hybrid ranking differ between engines.

`make failover` sends updates and searches while killing a process with SIGKILL.
It fsyncs attempted/acknowledged versions outside the database, leaves the process
down for **30 seconds**, restarts it, then checks every row and accepted version.
A request with an unknown commit outcome is recorded as ambiguous and is never
blindly replayed. Run `make up` again to reset data before baseline tests or
benchmarks after this destructive workload. Raw JSON/journals stay in gitignored
`results/<mode>/`. Do not run benchmark loads concurrently with corpus encoding
or other examples. One local Docker VM does not model independent hosts/zones.

No ports are published on the host; `make cli` opens a shell on the private
Compose network. Credentials are local development constants. `make status`
shows service roles and row counts. The shared corpus/cache are intentionally
retained for reuse; example cleanup does not delete another example's inputs.

Full-row checks cover every ID and metadata field. Vector coverage uses input
checksums, server dimension validation and exact-reference retrieval queries;
the harness does not read back every stored float from every copy.

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
replica; reads use the discovered primary. Each PostgreSQL node: **2 CPUs /
2 GiB**; each etcd: **0.5 CPU / 256 MiB**. Single mode runs PostgreSQL directly.
Psycopg binary COPY and bounded pools use maintained pgvector type adapters.
Each PostgreSQL container has **1 GiB /dev/shm**: the full parallel HNSW build
requested about 509 MiB and failed with Docker's default 64 MiB shared-memory
mount (`could not resize shared memory segment`). This is separate from disk
free space and stays within the 2 GiB container memory cap.

Limits: physical replication does not shard the catalog, and this client does
not load-balance reads across replicas. Strict sync can block writes rather than
risk an unreplicated ACK. Arbitrary joins/selective filters can change plans and
ANN recall; iterative scans are bounded. Candidate reranking may choose an exact
scan. EXPLAIN output records what the planner actually selected. Concurrent
failures, split brain, disk corruption and multi-host durability are outside this
single-VM test.

[pgvector docs/source](https://github.com/pgvector/pgvector/tree/10a73eceefe033ac944f91782c553b5fc67eccb9),
[inspected iterative HNSW scan](https://github.com/pgvector/pgvector/blob/10a73eceefe033ac944f91782c553b5fc67eccb9/src/hnswscan.c),
[Patroni replication modes](https://patroni.readthedocs.io/en/latest/replication_modes.html).

## Benchmark

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
patroni/{Dockerfile,patroni.yml}
app/{Dockerfile,requirements.txt,client.py}
docker-compose.yml
Makefile
README.md
results/
```

The fault runner refuses an existing `churn.jsonl`, `churn.json`, `fault.json` or
`reconciliation.json`; it never deletes an earlier ACK witness. Preserve and
diagnose a failed fixture before archiving its evidence and starting a separate
clean lifecycle. This guard does not add cross-run version reconciliation or an
independent reader to the existing churn loop.
