# Typesense product search

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

## Application-managed sharding

**This is application-managed sharding, not native Typesense distributed
sharding.** `MODE=sharded` starts **3 independent Raft groups**, each with
**3 replicas**: **9 Typesense servers**. Routing lives in `app/sharded.py`, inside
the workload client; there is no separate router service. Run the five lifecycle
commands with the same mode throughout, after completing teardown of other modes:

1. `MODE=sharded make docker-up` — start all nine servers and wait for readiness.
2. `MODE=sharded make up` — route/index the shared corpus by synthetic customer ID.
3. `MODE=sharded make test` — test routing, all metadata/copies, placement and isolation.
4. `MODE=sharded make down` — remove the nine servers, preserving named data volumes.
5. `MODE=sharded make docker-down` — remove this project's state and built images.

| Shard | Servers | Customer routing | Copies |
|---|---|---|---:|
| 0 | ts1, ts2, ts3 | `tenant % 3 == 0` | 3 |
| 1 | ts4, ts5, ts6 | `tenant % 3 == 1` | 3 |
| 2 | ts7, ts8, ts9 | `tenant % 3 == 2` | 3 |

Each server retains the existing **2 CPUs / 2 GiB** cap; the client has
**2 CPUs / 3 GiB**. These are CPU ceilings, not dedicated cores; the Docker VM
has 11 CPUs. Each server stores only its shard's products. `make test` exports
all **9 physical copies**, checking every metadata field, missing/duplicate IDs,
and `tenant % 3` placement. It also checks all **100 customers** in lexical,
dense and hybrid modes, including customers sharing the same shard.

Customer queries retain a `tenant` filter after routing; routing alone does not
isolate customers sharing a shard. Unscoped dense queries fan out to all three
clusters, fetch local top-10 results and merge by the same cosine distance.
Failure of any shard fails the whole query; partial results are not returned as
complete. ANN results remain approximate and are compared with the exhaustive
reference. Global lexical/hybrid search is rejected explicitly: no cross-cluster
text-score normalization, facet aggregation or global pagination is implemented.
Typesense's native `multi_search`/union API combines collections inside one
cluster; it is not a cross-cluster query coordinator.

`MODE=sharded make benchmark` measures global dense fan-out plus customer-routed
lexical/dense/hybrid queries, each at **1 and 8 clients**. Customer benchmark query
`i` selects tenant `1 + (i % 100)`; query text is not curated for that synthetic tenant.
`MODE=sharded FAULT_SHARD=0 make failover` discovers and kills that shard's current
leader while updates and global dense searches continue. `FAULT_SHARD` accepts
0, 1 or 2; default 0. The journal and final reconciliation cover every shard and
replica. Reload with `MODE=sharded make up` before a fresh fault run or baseline
check, because the fault workload changes inventory versions.

Limitations: the application owns routing, migration and rebalancing. Shard count
is fixed at 3; changing modulo would remap customers, so online scaling is not
implemented. Customer moves/tenant changes are rejected. A batch spanning shards
is not atomic; an error can leave earlier shard writes committed. The fsynced
attempt/ACK journal treats the entire unresolved batch as ambiguous, without
blind replay, and checks acknowledged versions after recovery. An unavailable
shard does not fail over into a different shard. All nodes share one Docker VM,
so replicas do not protect against loss of that host. This trusted benchmark
client and development API key are not a public authentication boundary;
production tenant access must enforce authorization and appropriate scoped keys.

Upstream evidence: [full dataset on every HA node](https://cloud-help.typesense.org/article/high-availability),
[multi-search and union API](https://typesense.org/docs/30.2/api/federated-multi-search.html),
[30.2 single Raft group initialization](https://github.com/typesense/typesense/blob/v30.2/src/raft_server.cpp#L103),
[30.2 local collection search dispatch](https://github.com/typesense/typesense/blob/v30.2/src/core_api.cpp#L1002).

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

Three **Typesense 30.2** full replicas form one Raft group; this is replication,
not three independent data shards. Each server is capped at **2 CPUs / 2 GiB**.
A derived official image adds curl for readiness probes. HNSW uses M=16,
ef_construction=128, search ef=256 and k=100; hybrid alpha=0.5. The collection
indexes text, vectors and tenant/stock/brand facets. The client checks every
import line; HTTP 200 alone does not mean every document was accepted.

The focused test imports two valid documents plus one dimension-mismatched
vector, requires exactly two successes, waits for replica visibility, and checks
typo/prefix search with a brand filter. The fault target discovers the Raft
leader before killing it and reconciles all three copies after rejoin.

Limits: every replica stores/indexes the full catalog; replicas increase capacity
for reads and availability, not storage sharding. Follower search visibility may
lag accepted writes. This local topology tests a single process failure, not
majority loss, network partitions, disk corruption or independent host failure.
Static private subnet 172.30.61.0/24 is overridable via NETWORK_PREFIX; use a
free subnet if it conflicts with an existing network.

[Vector API](https://typesense.org/docs/30.2/api/vector-search.html),
[HA guide](https://typesense.org/docs/guide/high-availability.html),
[inspected ranking/filter source](https://github.com/typesense/typesense/blob/v30.2/src/index.cpp).

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
native index construction; the Typesense cluster load overlapped scoped Docker
cache cleanup before the timed query run, so do not compare ingest rates as an
isolated hardware benchmark. Write recovery starts at the first failed new
attempt after the fault marker; maximum ACK gap also includes in-flight stalls.

### Single

100,000 products / 1,000 held-out queries; 100,000 rows matched the full-value oracle.
Observed setup load/index time: **87.52 s** (**1142.63 rows/s**).

| Mode | Clients | QPS | p50 ms | p95 ms | p99 ms | Dense recall@10 |
|---|---:|---:|---:|---:|---:|---:|
| lexical | 1 | 168.59 | 1.81 | 17.51 | 43.40 | — |
| lexical | 8 | 234.48 | 3.50 | 99.07 | 204.64 | — |
| dense | 1 | 362.14 | 2.62 | 3.11 | 3.26 | 0.9882 |
| dense | 8 | 769.61 | 6.28 | 37.75 | 41.99 | 0.9882 |
| hybrid | 1 | 123.78 | 3.88 | 19.44 | 44.59 | — |
| hybrid | 8 | 172.44 | 7.44 | 102.75 | 217.57 | — |

| Candidate ranking | nDCG@10 | Scorable / zero-IDCG queries |
|---|---:|---:|
| lexical | 0.3192 | 100 / 0 |
| dense | 0.5702 | 100 / 0 |
| hybrid | 0.6028 | 100 / 0 |

Correctness sample: 50 dense queries, mean recall@10 0.9900; 10 tenant-filtered queries, mean eligible-set recall@10 1.0000. No filter leaks or short dense/eligible results.

### Cluster

100,000 products / 1,000 held-out queries; 100,000 rows matched the full-value oracle.
Observed setup load/index time: **344.92 s** (**289.92 rows/s**).

| Mode | Clients | QPS | p50 ms | p95 ms | p99 ms | Dense recall@10 |
|---|---:|---:|---:|---:|---:|---:|
| lexical | 1 | 155.00 | 2.12 | 18.62 | 45.36 | — |
| lexical | 8 | 772.99 | 3.00 | 35.60 | 67.35 | — |
| dense | 1 | 283.29 | 3.30 | 4.06 | 4.56 | 0.9880 |
| dense | 8 | 1084.10 | 6.74 | 9.72 | 12.22 | 0.9888 |
| hybrid | 1 | 116.91 | 4.14 | 20.35 | 45.55 | — |
| hybrid | 8 | 567.75 | 5.48 | 43.00 | 73.26 | — |

| Candidate ranking | nDCG@10 | Scorable / zero-IDCG queries |
|---|---:|---:|
| lexical | 0.3192 | 100 / 0 |
| dense | 0.5702 | 100 / 0 |
| hybrid | 0.6028 | 100 / 0 |

Correctness sample: 50 dense queries, mean recall@10 0.9920; 10 tenant-filtered queries, mean eligible-set recall@10 1.0000. No filter leaks or short dense/eligible results.

| Crash check | Observed |
|---|---:|
| Killed process | ts2 |
| Acknowledged updates | 1640 |
| Acknowledged unique IDs | 1640 |
| Missing acknowledged versions | 0 |
| Successful searches while absent | 143 |
| Failed searches while absent | 0 |
| New update batches ACKed while absent | 139 |
| Failed-attempt to resumed-write ACK | 12.39 s |
| Largest ACK gap | 12.49 s |
| Restart to infrastructure health | 35.68 s |
| Ambiguous/failed update batches | 3 |

Infrastructure health is not replica catch-up time: the separate full reconciliation waited for all required copies/readers. No end-to-end host/disk failure guarantee is implied.


### Application-managed shards

Measured **2026-11-15** on the same Apple M4 Pro / native aarch64 Docker VM.
**9 servers, each 2 CPUs / 2 GiB; client 2 CPUs / 3 GiB**. These CPU ceilings
are oversubscribed on the 11-CPU VM. One warm run, 1,000 timed requests per case
after 16 warmups: **8,000 timed requests**, zero request failures. The fixture
contains 100,000 products and 100 customers; each row has three stored copies.
Global queries send three backend requests; customer queries send one. This is
not an equal-resource or equal-recall comparison with the three-server profile.

Parallel load/indexing: **146.46 s**, **682.77 rows/s**.
Every metadata field matched across all nine servers: **300,000 stored rows**,
**0 missing/duplicate/invalid/misplaced rows**. All **300 customer-isolation queries**
(100 customers × 3 modes) passed. The four routing regressions and seven shared
journal/oracle regressions also passed. Baseline correctness used 50 global dense
queries (mean recall@10 **0.9960**) and 10 customer-filtered
queries (mean eligible-set recall@10 **1.0000**).

| Shard | Rows on each replica | Replicas |
|---|---:|---:|
| 0 | 32,996 | 3 |
| 1 | 34,111 | 3 |
| 2 | 32,893 | 3 |

| Search scope / mode | Clients | QPS | p50 ms | p95 ms | p99 ms | Recall@10 |
|---|---:|---:|---:|---:|---:|---:|
| global / dense | 1 | 234.26 | 4.05 | 4.75 | 5.22 | 0.9958 |
| global / dense | 8 | 351.25 | 19.62 | 32.51 | 39.51 | 0.9955 |
| customer / lexical | 1 | 101.17 | 7.41 | 24.94 | 42.63 | — |
| customer / lexical | 8 | 652.09 | 8.70 | 30.86 | 47.77 | — |
| customer / dense | 1 | 60.08 | 15.63 | 22.65 | 27.79 | 1.0000 |
| customer / dense | 8 | 392.82 | 19.50 | 26.98 | 32.57 | 1.0000 |
| customer / hybrid | 1 | 39.64 | 21.97 | 41.30 | 60.31 | — |
| customer / hybrid | 8 | 257.82 | 27.10 | 53.19 | 75.93 | — |

Global dense recall covers all 1,000 timed queries per concurrency; customer
dense recall covers the first 100 timed queries against each eligible customer
catalog. Global candidate dense nDCG@10 was
**0.5702** on 100 scorable judged queries.
No customer lexical/hybrid relevance score is claimed: tenants are synthetic
and query text was selected independently of customer membership.

The full-catalog three-replica baseline measured **1,084.10 QPS** for global
dense search at 8 clients versus **351.25 QPS** here: the sharded run achieved
**0.324×** the baseline QPS (baseline **3.09×** higher). Recall also differs
(**0.9888** versus **0.9955**). This run demonstrates storage partitioning and
recovery, not a throughput improvement.

| Shard-0 leader crash | Observed |
|---|---:|
| Killed elected leader | ts3 |
| Requested absence | 30 s |
| Acknowledged updates | 2,070 |
| Acknowledged unique IDs | 2,070 |
| Missing acknowledged versions, all 9 servers | 0 |
| Global searches while leader absent | 175 |
| Failed searches while leader absent | 0 |
| New update batches ACKed while absent | 154 |
| First new-batch ACK after fault marker | 10.39 s |
| Largest ACK gap | 10.62 s |
| Restart to infrastructure health | 10.67 s |
| Ambiguous/failed batches | 21 |
| Surviving server containers unchanged | 8 |
| First failed new attempt to resumed ACK | 6.40 s |

During election/rejoin, 21 unresolved/failed batches included HTTP 503 and
`illegal status line: bytearray(b'HTTP/1.1 0 ')`. They were journaled as ambiguous
and were not blindly replayed. Full reconciliation checked all
**300,000 stored metadata rows** and ACKed
versions after rejoin. This tests one shard-0 leader process crash, not an entire
shard losing quorum, a disk failure, or a network partition. Shard-1/shard-2 leader
crashes are selectable with `FAULT_SHARD` but were not separately run.


## Directory structure

```text
server/Dockerfile
scripts/config.py
config/generated/{nodes,nodes1,nodes2}
app/{Dockerfile,client.py,sharded.py,test_sharding.py}
docker-compose.yml
Makefile
README.md
results/
```
