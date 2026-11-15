# Vespa phased product retrieval

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
down for **180 seconds**, restarts it, then checks every row and accepted version.
A request with an unknown commit outcome is recorded as ambiguous and is never
blindly replayed. Run `make up` again to reset data before baseline tests or
benchmarks after this destructive workload. Vespa uses a **300-second** fault workload;
`DURATION` and `NODE_DOWN_SECONDS` are Make variables. Updates use a **3-second
server deadline / 5-second client deadline**, so a stalled request does not stop
new attempts for the entire test. Raw JSON/journals stay in gitignored
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

Vespa **8.781.11**, native ARM64, runs **three content nodes** with redundancy
3 and three searchable copies. Each container also hosts a configuration server,
cluster controller and query/feed container. Three configuration servers retain
quorum across a single node loss. Each is capped at **2 CPUs / 4 GiB**; the Java
query heap is 512 MiB, configuration heap 256 MiB, controller heap 128 MiB and
configuration-proxy heap 64 MiB. These are small local-lab heaps, not production
sizing guidance. Single mode generates one of each role and redundancy 1.

The schema combines BM25 text and 384-dimensional angular HNSW vectors with
M=16 / insertion exploration=128. Queries request 100 nearest-neighbor
candidates with 256 additional exploration hits. Four ranking profiles expose
lexical, dense, hybrid and global RRF reranking of at most 100 candidates. Hybrid
scores combine bounded BM25 and closeness. Reranking reuses match features;
no external model/service runs in the timed request.

Vespa distributes native document buckets and copies. With three nodes and
redundancy 3, **each node stores all 100,000 products**. This profile does not
partition storage across nodes; a storage-sharded/grouped topology remains
unimplemented. This is not tenant-modulo sharding. The row visitor verifies the entire logical catalog. Native `vespa-get
--replica-override` reads every ID and metadata field from each physical copy,
including accepted versions after node loss. Node metrics also expose
stored/ready/active document counts. Query profiles fail the check on
incomplete coverage instead of silently returning partial results.

Limits: all roles share three containers on one Docker VM. Bucket redundancy,
ready copies and active search ownership differ; counts must be interpreted with
Vespa's native model. Full searchable replicas cost three vector/text indexes. This flat group exposes
native bucket ownership and query fan-out; upstream recommends grouped
distribution for ANN read scaling. That topology comparison is not implemented.
SIGKILL is process loss, not disk loss or simultaneous power loss. Feed blocking
is preserved: a nearly full Docker filesystem returns HTTP 507 and must be fixed
before ingestion. Neither a deployment ACK nor config-server health alone means
the search application is ready; the client waits for each query endpoint.

[Hybrid search tutorial](https://docs.vespa.ai/en/learn/tutorials/hybrid-search),
[phased ranking](https://docs.vespa.ai/en/ranking/phased-ranking.html),
[content configuration](https://docs.vespa.ai/en/reference/applications/services/content.html),
[native replica-selection source](https://github.com/vespa-engine/vespa/blob/v8.781.11/storage/src/vespa/storage/distributor/operations/external/getoperation.cpp),
[inspected global ranker](https://github.com/vespa-engine/vespa/blob/v8.781.11/container-search/src/main/java/com/yahoo/search/ranking/GlobalPhaseRanker.java).

The pinned controller source defaults to a **30-second ZooKeeper session timeout**,
a **60-second master cooldown**, and a **30-second initial state-broadcast
wait**. Losing the node that also hosts the active
controller can therefore block writes until that safety delay and state gathering
complete. The crash test leaves it down for 180 seconds to observe a new leader,
rather than restoring it before the election can finish. Initial 30/75/120-second
experiments observed read availability but no write ACKs during the absence;
those are retained as diagnostics, not successful failover results.
Native CLI verification explicitly sets UTF-8 output: the image's default CLI
charset rendered non-ASCII product text as `?` even though stored text was intact.

[Inspected election safety checks](https://github.com/vespa-engine/vespa/blob/v8.781.11/clustercontroller-core/src/main/java/com/yahoo/vespa/clustercontroller/core/MasterElectionHandler.java),
[pinned timeout defaults](https://github.com/vespa-engine/vespa/blob/v8.781.11/configdefinitions/src/vespa/fleetcontroller.def).


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
Observed setup load/index time: **330.18 s** (**302.87 rows/s**).

| Mode | Clients | QPS | p50 ms | p95 ms | p99 ms | Dense recall@10 |
|---|---:|---:|---:|---:|---:|---:|
| lexical | 1 | 389.39 | 2.41 | 3.65 | 5.47 | — |
| lexical | 8 | 803.47 | 5.22 | 52.22 | 57.89 | — |
| dense | 1 | 269.92 | 3.46 | 4.47 | 5.13 | 0.9921 |
| dense | 8 | 542.22 | 7.72 | 54.02 | 59.14 | 0.9921 |
| hybrid | 1 | 236.93 | 4.08 | 5.45 | 8.03 | — |
| hybrid | 8 | 390.60 | 7.54 | 75.54 | 78.75 | — |
| rerank | 1 | 236.69 | 4.06 | 5.71 | 8.68 | — |
| rerank | 8 | 379.81 | 7.57 | 75.58 | 79.83 | — |

| Candidate ranking | nDCG@10 | Scorable / zero-IDCG queries |
|---|---:|---:|
| lexical | 0.5491 | 100 / 0 |
| dense | 0.5702 | 100 / 0 |
| hybrid | 0.5917 | 100 / 0 |
| rerank | 0.5910 | 100 / 0 |

Correctness sample: 50 dense queries, mean recall@10 1.0000; 10 tenant-filtered queries, mean eligible-set recall@10 1.0000. No filter leaks or short dense/eligible results.

### Cluster

100,000 products / 1,000 held-out queries; 100,000 rows matched the full-value oracle.
Observed setup load/index time: **377.72 s** (**264.75 rows/s**).

| Mode | Clients | QPS | p50 ms | p95 ms | p99 ms | Dense recall@10 |
|---|---:|---:|---:|---:|---:|---:|
| lexical | 1 | 274.76 | 3.49 | 4.72 | 6.05 | — |
| lexical | 8 | 834.78 | 7.45 | 25.48 | 35.80 | — |
| dense | 1 | 117.32 | 8.29 | 10.16 | 10.69 | 0.9974 |
| dense | 8 | 288.33 | 19.89 | 61.06 | 66.35 | 0.9974 |
| hybrid | 1 | 111.69 | 8.88 | 10.84 | 12.60 | — |
| hybrid | 8 | 254.50 | 23.18 | 62.80 | 71.19 | — |
| rerank | 1 | 110.73 | 8.96 | 11.01 | 12.78 | — |
| rerank | 8 | 251.36 | 26.16 | 63.16 | 70.51 | — |

| Candidate ranking | nDCG@10 | Scorable / zero-IDCG queries |
|---|---:|---:|
| lexical | 0.5493 | 100 / 0 |
| dense | 0.5702 | 100 / 0 |
| hybrid | 0.5917 | 100 / 0 |
| rerank | 0.5908 | 100 / 0 |

Correctness sample: 50 dense queries, mean recall@10 1.0000; 10 tenant-filtered queries, mean eligible-set recall@10 1.0000. No filter leaks or short dense/eligible results.

| Crash check | Observed |
|---|---:|
| Killed process | node0 |
| Acknowledged updates | 4880 |
| Acknowledged unique IDs | 4880 |
| Missing acknowledged versions | 0 |
| Successful searches while absent | 212 |
| Failed searches while absent | 0 |
| New update batches ACKed while absent | 173 |
| Failed-attempt to resumed-write ACK | 119.75 s |
| Largest ACK gap | 123.01 s |
| Restart to infrastructure health | 11.19 s |
| Ambiguous/failed update batches | 39 |

Infrastructure health is not replica catch-up time: the separate full reconciliation waited for all required copies/readers. No end-to-end host/disk failure guarantee is implied.


### Native physical-copy verification

| Phase | Node | Complete metadata rows checked | Missing ACK versions | Invalid rows |
|---|---:|---:|---:|---:|
| cluster / fault | 0 | 100,000 | 0 | 0 |
| cluster / fault | 1 | 100,000 | 0 | 0 |
| cluster / fault | 2 | 100,000 | 0 | 0 |
| single / baseline | 0 | 100,000 | 0 | 0 |

Each native export checks the document URI against its payload ID before the
full metadata/version oracle. These checks read the requested storage replica,
not just whichever active owner answers a search. Raw exports and summaries are
in `results/<mode>/.native-*.json` and `copy-*.json`; vectors are excluded.
The fault test also verified that both surviving containers never restarted.

The fixed global RRF profile did not improve the 100-query candidate sample over
hybrid first-phase scoring (cluster nDCG@10 **0.5908 vs 0.5917**). This example
exposes ranking cost/quality controls; a reranking phase alone is not a relevance
guarantee. Flat-group dense throughput was also below the single-node baseline;
see the reported recall, fan-out and resource differences above.

## Directory structure

```text
application/schemas/product.sd
application/generated/{hosts.xml,services.xml,schemas/product.sd}
scripts/{config.py,copies.py}
app/{Dockerfile,client.py}
docker-compose.yml
Makefile
README.md
results/
```
