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

## Sharded storage profile

`MODE=sharded` uses native Vespa grouped distribution: **3 replica groups × 3
content nodes**, plus **3 separate control/query nodes**. Each group stores one
complete copy of the catalog, partitioned by native document buckets across its
three content nodes. Every document has **3 copies total**, one in each group;
no content node should store the entire catalog. Bucket ownership is managed by
Vespa, not a customer-modulo router. A customer's products may span content nodes.

Complete teardown of another profile before changing modes. Use the same mode
throughout the lifecycle:

1. `MODE=sharded make docker-up` — start 3 control/query and 9 content hosts.
2. `MODE=sharded make up` — deploy the grouped application and feed/index products.
3. `MODE=sharded make test` — check full values, coverage, filters and physical copies.
4. `MODE=sharded make down` — remove all 12 services, retaining named volumes.
5. `MODE=sharded make docker-down` — remove this project's state and built image.

| Role | Hosts | Cap per host | Application role |
|---|---|---|---|
| Control/query | node0–node2 | 2 CPUs / 2 GiB | Config servers, controllers, query/feed API |
| Replica group 0 | content0–content2 | 1 CPU / 1.5 GiB | One full catalog split across 3 content nodes |
| Replica group 1 | content3–content5 | 1 CPU / 1.5 GiB | A second full catalog split across 3 content nodes |
| Replica group 2 | content6–content8 | 1 CPU / 1.5 GiB | A third full catalog split across 3 content nodes |
| Test client | tools | 2 CPUs / 3 GiB | Fixture, correctness, benchmark, ACK journal |

The image normally enforces a **4 GiB startup floor**. Only the sharded lab
profile sets `VESPA_IGNORE_NOT_ENOUGH_MEMORY=true` to allow the smaller role-specific
host limits above. Cgroup memory limits and native disk/memory feed guards remain
enabled; these small heaps are not production sizing guidance.

The content cluster sets `min-redundancy=3`, `searchable-copies=3`, and root
`distribution partitions="1|1|*"`. `min-node-ratio-per-group=1` takes an
incomplete group out of service instead of forcing its two surviving nodes to
absorb the missing node's storage. With three groups, two complete groups can
continue serving. Queries use Vespa's dispatcher across a complete
group. Control/query hosts have no content role. The content-host infrastructure
healthcheck uses native `vespa-ping-configproxy`; `make up` separately waits for
query coverage. Config-proxy health alone does not establish search readiness.

`MODE=sharded make benchmark` measures all four ranking profiles at 1 and 8
clients against the same 100,000-product / 1,000-query corpus. Run benchmarks
before a destructive fault run. `MODE=sharded make failover` kills **content0**
while updates and searches continue, leaves it absent for **60 seconds**, then
restores it and reconciles acknowledged versions. Default client duration is
**120 seconds**. Set `FAULT_SERVICE=content0` through `content8` to choose another
data host. Controllers remain alive during this test; the existing `MODE=cluster`
test separately covers losing a host that also runs a controller.

Native copy verification sends every catalog ID through `vespa-get
--replica-override` for every content node, checks the request/reply ID ordering,
and combines each group's three physical exports. Each group must contain all
IDs exactly once with matching metadata/ACK versions; each node must contain a
proper subset. The native CLI prints `Document not found.` outside JSON for an
absent copy; only that exact protocol line is decoded as an absent reply. Bad
reply counts, malformed JSON, wrong IDs and wrong payloads fail the check.
Vectors are excluded from physical exports; dimension validation and independent
exact-reference searches cover vector retrieval. The tests use native routing
and do not independently reimplement Vespa's bucket-placement hash.

Limits: this is one Docker VM, with oversubscribed CPU ceilings and small lab
heaps. It does not test independent-host failure, disk corruption, network
partition or online topology changes. A 12-container topology is not an
equal-resource comparison with the existing three-container profile. Failure
checks distinguish infrastructure health, search coverage and final physical
copy reconciliation. Reload before another fault run to reset inventory versions.

`MODE=sharded make deploy` applies application configuration without reloading
products; `make up` remains the complete deploy/feed entry point.

Upstream: [grouped distribution and searchable copies](https://docs.vespa.ai/en/reference/applications/services/content.html),
[topology tradeoffs](https://docs.vespa.ai/en/performance/topology-and-resizing),
[group availability threshold](https://docs.vespa.ai/en/reference/applications/services/content.html#min-node-ratio-per-group),
[Docker startup memory floor](https://docs.vespa.ai/en/operations/self-managed/docker-containers.html#memory),
[pinned group/redundancy validation](https://github.com/vespa-engine/vespa/blob/v8.781.11/config-model/src/main/java/com/yahoo/vespa/model/content/IndexedHierarchicDistributionValidator.java),
[pinned native-get reply formatting](https://github.com/vespa-engine/vespa/blob/v8.781.11/vespaclient-java/src/main/java/com/yahoo/vespaget/DocumentRetriever.java),
[pinned config-proxy probe](https://github.com/vespa-engine/vespa/blob/v8.781.11/config/src/apps/vespa-ping-configproxy/pingproxy.cpp).

## Workload and measurements

The common [ESCI fixture](../../datasets/esci/README.md) defaults to **100,000
products**, **1,000 held-out queries**, **384-dimensional** normalized real text
embeddings, **100 synthetic tenants**, and top **10** results. Stock/price/tenant
attributes are deterministic synthetic data. Each engine receives identical
vectors and products. These are product-text retrieval tests, not LLM/RAG tests.

`make benchmark` measures lexical, dense, hybrid and global-RRF reranked searches
at **1 and 8 clients**: **16 warmup requests**, then **1,000 timed queries per case**. Set
`BENCH_QUERIES`, `QUALITY_QUERIES` (default 100), `CHECK_QUERIES` (default 50).
Requests use precomputed vectors; tokenization/encoding is excluded. Candidate
nDCG uses graded ESCI judgments with E=1, S=0.1, C=0.01, I=0; zero-IDCG queries
are excluded and counted. Dense recall uses an independent exhaustive NumPy
reference with boundary ties accepted. Quality and ANN recall measure different
things. Lexical tokenization and native hybrid ranking differ between engines.

`make failover` sends updates and searches while killing a process with SIGKILL.
It fsyncs attempted/acknowledged versions outside the database, leaves the process
down for **180 seconds** in `MODE=cluster` (**60 seconds** in `MODE=sharded`),
restarts it, then checks every row and accepted version.
A request with an unknown commit outcome is recorded as ambiguous and is never
blindly replayed. Run `make up` again to reset data before baseline tests or
benchmarks after this destructive workload. The default fault workload lasts
**300 seconds** in `MODE=cluster` and **120 seconds** in `MODE=sharded`;
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

The default `MODE=cluster` runs Vespa **8.781.11**, native ARM64, with
**three content nodes**, redundancy
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
partition storage across nodes; the separate `MODE=sharded` profile partitions
storage across nine content nodes. This is not tenant-modulo sharding. The row
visitor verifies the entire logical catalog. Native `vespa-get --replica-override`
reads every ID and metadata field from each physical copy,
including accepted versions after node loss. Node metrics also expose
stored/ready/active document counts. Query profiles fail the check on
incomplete coverage instead of silently returning partial results.

Limits: all roles share three containers on one Docker VM. Bucket redundancy,
ready copies and active search ownership differ; counts must be interpreted with
Vespa's native model. Full searchable replicas cost three vector/text indexes. This flat group exposes
native bucket ownership and query fan-out; upstream recommends grouped
distribution for ANN read scaling. The `MODE=sharded` profile implements grouped distribution with separate control/query hosts.
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

### Native storage sharding

Measured **2026-11-15**, Apple M4 Pro / native aarch64 Docker VM, **11 CPUs /
24.42 GiB**. Three control/query hosts each have **2 CPUs / 2 GiB**; nine content
hosts each have **1 CPU / 1.5 GiB**. The client has **2 CPUs / 3 GiB**. These are
oversubscribed CPU ceilings. One warm run per case, no other database example
active during measurement, precomputed embeddings. Raw samples and caps are in
`results/sharded/`.

Initial deploy/feed/index plus logical-row verification: **430.61 s**,
**232.23 rows/s**; feed/index/visitor portion **394.85 s**.
A subsequent configuration-only `make deploy` enabled whole-group failover before
correctness, benchmarks and the crash. The deployment reported **0 restart, 0
refeed and 0 reindex actions**. Initial setup timing is not an isolated ingestion
benchmark.

Physical verification ran **900,000 directed native gets per phase**: **300,000
present metadata copies** and **600,000 absent replies**, both before and after
failure. Each group contained all **100,000 product IDs exactly once**. All
metadata values and acknowledged versions matched; **0 missing, duplicate or
invalid rows**. The three native-reply regression tests and seven shared negative
contract tests passed. The final runtime check found **12 healthy services** with
**0 cgroup OOM events/kills**.

| Replica group | Content node | Baseline stored rows | After recovery |
|---|---|---:|---:|
| 0 | content0 | 33,102 | 33,102 |
| 0 | content1 | 33,529 | 33,529 |
| 0 | content2 | 33,369 | 33,369 |
| 1 | content3 | 34,537 | 34,537 |
| 1 | content4 | 32,984 | 32,984 |
| 1 | content5 | 32,479 | 32,479 |
| 2 | content6 | 33,007 | 33,007 |
| 2 | content7 | 33,533 | 33,533 |
| 2 | content8 | 33,460 | 33,460 |

Baseline query correctness: **50 global dense queries**, mean recall@10
**1.0000**; **10 tenant-filtered queries**, mean eligible-set recall@10
**1.0000**. Every benchmark case timed **1,000 requests** after **16
warmups**, totaling **8,000 timed requests**, with **0 request failures**. Dense
recall below covers all 1,000 timed queries at each concurrency.

| Mode | Clients | QPS | p50 ms | p95 ms | p99 ms | Dense recall@10 |
|---|---:|---:|---:|---:|---:|---:|
| lexical | 1 | 261.02 | 3.67 | 4.96 | 6.44 | — |
| lexical | 8 | 948.55 | 7.95 | 12.63 | 15.87 | — |
| dense | 1 | 178.96 | 5.30 | 6.29 | 7.22 | 0.9963 |
| dense | 8 | 561.30 | 13.05 | 19.98 | 24.31 | 0.9966 |
| hybrid | 1 | 171.89 | 5.70 | 6.90 | 8.59 | — |
| hybrid | 8 | 553.45 | 14.01 | 20.94 | 24.31 | — |
| rerank | 1 | 169.25 | 5.78 | 7.13 | 8.99 | — |
| rerank | 8 | 546.84 | 14.17 | 21.03 | 24.70 | — |

| Candidate ranking | nDCG@10 | Scorable / zero-IDCG queries |
|---|---:|---:|
| lexical | 0.5528 | 100 / 0 |
| dense | 0.5702 | 100 / 0 |
| hybrid | 0.5929 | 100 / 0 |
| rerank | 0.5911 | 100 / 0 |

At 8 clients, dense throughput was **561.30 QPS** versus **288.33 QPS**
for the flat three-container profile (**1.95×**); p99 was **24.31 ms** versus
**66.35 ms** (**2.73×** lower). Recall was **0.9966** versus **0.9974**.
The server ceilings also increased from **6 CPUs / 12 GiB** to **15 CPUs /
19.5 GiB**, across 12 rather than 3 containers on the same 11-CPU VM. These
measurements are not a resource-normalized scaling comparison.

| Content-node crash | Observed |
|---|---:|
| Killed service / signal | content0 / SIGKILL |
| Requested absence / workload duration | 60 s / 120 s |
| Acknowledged updates / unique IDs | 2,960 / 2,960 |
| Missing acknowledged versions, all 9 content nodes | 0 |
| New update batches ACKed while absent | 124 |
| Global searches while absent / failed searches while absent | 129 / 0 |
| First new-batch ACK after fault marker | 9.70 s |
| First failed new attempt to resumed ACK | 6.60 s |
| Largest ACK gap | 9.85 s |
| Restart to config-proxy health | 5.66 s |
| Ambiguous/failed batches | 5 |
| Surviving server containers unchanged | 11 |

The content-only crash kept all three controllers online. The earlier
`MODE=cluster` experiment kills a host that also runs a controller; its election
delays exercise a different failure path. Config-proxy health does not establish
copy catch-up: the final native readback verified every stored metadata copy and
accepted version separately. This crash targeted content0; the other selectable
content hosts were not individually killed.

## Directory structure

```text
application/schemas/product.sd
application/generated/{hosts.xml,services.xml,schemas/product.sd}
scripts/{config.py,copies.py}
app/{Dockerfile,client.py,native.py,test_native.py}
docker-compose.yml
Makefile
README.md
results/
```
