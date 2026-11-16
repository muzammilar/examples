# LanceDB versioned product retrieval

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

LanceDB **0.41.0** is an embedded OSS library. Cluster mode here means **one
writer + three read-only HTTP serving workers** over **one shared local volume**.
It is not a replicated database or distributed consensus deployment. Each worker
has **2 CPUs / 3 GiB**; the writer has the same cap. Single mode queries the writer.

The maintained Python SDK and Arrow fixed-size lists build an IVF_FLAT cosine
index with 256 partitions, a tenant B-tree, and native FTS. Normal search probes
32 partitions; filtered candidate/tenant searches probe all 256, avoiding short
results at high selectivity. Hybrid search uses the SDK's native fusion. Opening
fresh table handles with zero consistency interval makes version refresh explicit.
The small HTTP wrapper uses the Python standard library; it serializes mutations
at the sole writer and rejects writes on reader endpoints.

The focused test pins an old version, updates a row, checks old/latest views,
compacts while retaining 30 days of history, reopens the old version and restores
the baseline. `make maintenance` runs SDK optimization. `make failover` crashes
the writer: reads can continue but writes cannot succeed until it returns. Use
`FAULT_SERVICE=reader1 make failover` for a separate query-worker crash.

Limits: the shared volume and writer are single failure points; no automatic
writer election, replication or cross-host filesystem guarantees are claimed.
Independent workers may improve serving concurrency but share CPU, filesystem
and page cache. Version history consumes disk until retention cleanup; freshly
updated rows can remain unindexed and dense searches include the SDK's normal
unindexed-tail path. Fast-search skipping of that tail is deliberately disabled.
The churn test changes inventory metadata and carries the original vectors;
model re-embedding and FTS freshness after updates are not evaluated.
These HTTP timings include the wrapper; they are not direct embedded SDK timings.

[Embedded documentation](https://docs.lancedb.com/_llms/embedded.txt),
[inspected SDK query implementation](https://github.com/lancedb/lancedb/blob/v0.41.0/python/python/lancedb/query.py),
[version/maintenance implementation](https://github.com/lancedb/lancedb/blob/v0.41.0/python/python/lancedb/table.py).

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

Infrastructure health is not replica catch-up time: the separate full reconciliation waited for all required copies/readers. No end-to-end host/disk failure guarantee is implied.


### Separate reader failure

From a reset 100,000-row catalog, `FAULT_SERVICE=reader1 make failover` killed
only reader1. The writer, reader2 and reader3 retained their container IDs and
start times. While reader1 was absent, **218 update batches** and
**217 searches** succeeded, with **0 failed mutation batches**,
**0 failed searches**, and **0 missing acknowledged versions**.
The independent raw journal is in `results/cluster/reader-loss/`; the writer
isolation rerun is in `results/cluster/writer-isolation/`. Reset data between
fault experiments; a second journal does not describe a previous workload.

Version-test compaction took **1.633 s** while retaining the pinned version.
The test also proved the updated row remained visible through the unindexed
vector tail and that a read-only worker rejected mutation.

## Directory structure

```text
app/{Dockerfile,requirements.txt,server.py,client.py}
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
