# Typesense product search

## Quick start

Run from this directory with Docker Compose v2, Make and Python 3.11+ available. Corpus
preparation downloads 1.16 GB of verified Parquet plus the pinned ONNX model;
encoding is a separate first-run cost. This example owns its cache and prepared corpus.

1. `make docker-up` — start the local infrastructure and wait for readiness.
2. `make up` — bind/load an empty fixture once, or verify retained data without reloading.
3. `make test` — check complete rows, filters, retrieval and the system-specific behavior.
4. `make down` — delete the example collections on every replica, leaving infrastructure running.
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
2. `MODE=sharded make up` — route/index the example-local corpus by synthetic customer ID.
3. `MODE=sharded make test` — test routing, all metadata/copies, placement and isolation.
4. `MODE=sharded make down` — delete the example collections on all nine servers.
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
replica. Every fault run preserves earlier intents and acknowledgements. `make up` verifies
the retained history; it never resets modified inventory to version zero.

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
blindly replayed. The controller verifies retained inventory before benchmarks; `make up` never
resets it. Raw JSON/journals stay in gitignored `results/production/<mode>/`. Do not run benchmark loads concurrently with corpus encoding
or other examples. One local Docker VM does not model independent hosts/zones.

No ports are published on the host; `make cli` opens a shell on the private
Compose network. Credentials are local development constants. `make status`
shows the project containers. The example-local corpus/cache is retained; cleanup
does not delete another example's inputs.

Full-copy checks export every ID, metadata field and all 384 float32 vector
coordinates from every node URL, checking shard placement and retained ACKs.
The pinned source dispatches these reads locally. This proves API-visible copies,
not independent decoding of RocksDB files. ANN recall is checked separately.

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

## Preserved-history production backfill (2026-11-17)

The replicated core backfill is qualified with the failures and limits below.
Single-node lifecycle/persistence is also qualified; extended unrun targets are not passing results.
The existing 2026-11-15 benchmarks below use the earlier controller/readiness
configuration and are historical results, not measurements of this backfill.

The independent Go `inventory-workload/` writer and reader run as Compose
services. Each has a 1 CPU / 512 MiB cap; the Python native-copy verifier has
2 CPUs / 3 GiB. The writer sends one batch at a time with a 100 ms delay between batches; it
updates stock/price/version in batches (`UPDATE_BATCH`,
default 10), records every intent and ordered per-record import outcome with
fsync, and never retries mutations blindly. A partial/truncated HTTP response
preserves known ACKs and marks only unresolved rows ambiguous. Reader failover
retains failed attempts; search/GETs, stale versions and payload errors remain
separate observations. `DURATION_SECONDS=1800` is a safety ceiling, not a timed
success condition. The controller must stop both roles after reconciliation.

| Check | Observed |
|---|---|
| Retained cluster catalogue and vectors | 300,000 physical documents; 115,200,000 coordinates, all matched |
| Search quality/isolation | 450 queries; 100 tenants; mean dense recall@10 0.992; 0 leaks/duplicates |
| Initial default-policy leader crash | **Failed**: recovering follower returned 9 dense hits where 10 were required; 3,770 ACK / 1,710 ambiguous updates retained |
| Later read-only reconciliation of that failure | All copies matched; no lasting acknowledged data loss/corruption observed |
| Leader crash with `HEALTHY_READ_LAG=0` | Passed: 60,170 ACK / 3,010 ambiguous updates; 154 failed read/search operations; 2 stale GETs |
| Live-prefix / final reconciliation in that run | 551.60 s / 30.82 s; all 300,000 copies matched after repair |
| First follower run | **Failed harness run**: aborted after finding that its loaded controller sorted journals by fault name; 60,850 ACK / 3,860 ambiguous updates preserved; 145 failed operations, 0 stale/wrong events |
| Corrected-controller reconciliation across all three runs | All 124,790 ACKs checked against 300,000 physical documents / 115.2M coordinates in 29.32 s; no missing/corrupt/stale/misplaced rows |
| Retained rollout to read lag 10 | Passed in 503.67 s; all three original volume births unchanged; every document/vector on every copy verified after each serial node change |
| Corrected follower crash, read lag 10 | Passed: 17,660 ACK / 6,860 ambiguous updates; 1,291 searches + 1,291 GETs; 0 whole-operation failures/stale/wrong events |
| Follower live-prefix / final proof | 31.59 s / 30.56 s; all 300,000 physical documents and 115.2M coordinates matched |
| Permanent follower-volume loss, read lag 10 | Passed: 13,230 ACK / 4,350 ambiguous updates; 1,226 searches + 1,226 GETs; 0 whole-operation read failures/stale/wrong events; survivors unchanged |
| Empty-volume live-prefix / final proof | 32.07 s / 29.97 s; all 300,000 physical documents and 115.2M coordinates matched |
| Rebuilt-member retained-disk crash, read lag 10 | Passed: 18,730 ACK / 7,440 ambiguous updates; 1,364 searches + 1,364 GETs; 0 whole-operation read failures/stale/wrong events |
| Rebuilt-member live-prefix / final proof | 31.89 s / 29.93 s; every document/vector on all three copies matched |
| Leader loss after rebuild, read lag 10 | Passed: 12,600 ACK / 4,660 ambiguous updates; 1,536 searches + 1,536 GETs; 0 whole-operation read failures/stale/wrong events |
| Leader live-prefix / final proof | 30.92 s / 30.77 s; every document/vector on all three copies matched |
| Aggregate retained-history verification/report | 187,010 ACKs / 218,900 attempted updates across 7 runs; all 300,000 physical documents/115.2M coordinates matched; the 2 failed runs remain failed |
| Whole-stack retained-volume reopen | **Failed after 383.55 s**: Compose marked a still-replaying server unhealthy; separate no-replay reconciliation later matched every copy |

### Current application-sharded recovery

Measured **2026-11-17**, same example-local corpus and pinned 30.2 binary.
**3 independent Raft groups × 3 replicas**, each capped at **2 CPUs / 2 GiB**;
18 aggregate CPU ceilings share the 11-CPU VM. The live writer and reader each
have **1 CPU / 512 MiB**; the verifier has **2 CPUs / 3 GiB**. Sharded setup took
**215.05 s** after `docker-up` (**12.27 s**). Every node URL was checked directly:
shard 0 stores **32,996** products per copy, shard 1 **34,111**, shard 2 **32,893**.
All **300,000 physical documents / 115.2M vector coordinates** matched.

The serial production suite passed all **12 core faults**, retaining **88,496
ACKed / 5,944 ambiguous updates**. There were **0 whole-operation read failures**
and **1 stale GET**; failed endpoint calls handled by read failover remain in raw
journals and reports. Every case verified all nine copies, restored native queues
and commit watermarks, and checked untouched survivor processes. Each empty-volume
replacement had a new creation time and explicit UUID label. Final quality:
**350 queries / 100 tenants**, mean dense recall@10 **0.996**, 0 leaks/duplicates.
These are closed-loop fault observations, not throughput benchmarks.

| Shard | Fault | ACK rows | Ambiguous rows | Searches | Fresh GETs | Failed operations | Stale GETs |
|---|---|---:|---:|---:|---:|---:|---:|
| 0 | leader | 6,745 | 335 | 1,527 | 1,526 | 0 | 1 |
| 0 | follower | 7,552 | 528 | 1,582 | 1,582 | 0 | 0 |
| 0 | disk | 7,590 | 490 | 1,382 | 1,382 | 0 | 0 |
| 0 | rebuilt | 7,196 | 484 | 1,346 | 1,346 | 0 | 0 |
| 1 | leader | 6,830 | 450 | 1,403 | 1,403 | 0 | 0 |
| 1 | follower | 7,862 | 588 | 1,634 | 1,634 | 0 | 0 |
| 1 | disk | 7,878 | 572 | 1,419 | 1,419 | 0 | 0 |
| 1 | rebuilt | 7,519 | 531 | 1,635 | 1,635 | 0 | 0 |
| 2 | leader | 6,806 | 434 | 1,450 | 1,450 | 0 | 0 |
| 2 | follower | 7,349 | 521 | 1,196 | 1,196 | 0 | 0 |
| 2 | disk | 7,557 | 503 | 1,654 | 1,654 | 0 | 0 |
| 2 | rebuilt | 7,612 | 508 | 1,241 | 1,241 | 0 | 0 |

An additional shard-0 peer-loss case passed after all replacements: rebuilt `ts3`
remained online while older `ts2` was fenced. The affected shard ACKed **405 updates
while ts2 was absent**. That case retained **7,163 ACK / 487 ambiguous updates**,
1,512 searches and fresh GETs, with no whole-operation failures/stale reads. Final
report across **13 passed cases** reconciled **95,659 ACK / 102,090 attempted
updates** (6,431 ambiguous), all nine copies and every vector coordinate. The
retained history crossed the 100,000-ID wraparound without losing prior ACK floors.

The sharded warm benchmark ran after the 12 core cases, before this extra peer
case. Same resource caps, 16 warmups then 1,000 timed requests per case:
**8,000 timed requests, 0 failures**. Global dense sends three backend requests
per logical query; a customer query sends one. Customer dense recall is sampled
on 100 queries. This is not an equal-resource comparison with the 3-server profile.

| Query | Clients | QPS | p50 ms | p99 ms | Dense recall@10 |
|---|---:|---:|---:|---:|---:|
| global dense | 1 | 240.92 | 3.96 | 4.84 | 0.9955 |
| global dense | 8 | 359.11 | 19.17 | 37.76 | 0.9957 |
| customer lexical | 1 | 101.44 | 7.30 | 43.20 | — |
| customer lexical | 8 | 683.96 | 8.35 | 48.20 | — |
| customer dense | 1 | 73.35 | 13.59 | 15.61 | 1.0000 |
| customer dense | 8 | 440.97 | 17.55 | 30.14 | 1.0000 |
| customer hybrid | 1 | 43.96 | 20.17 | 53.96 | — |
| customer hybrid | 8 | 275.39 | 25.20 | 76.46 | — |

The fresh sharded lifecycle also passed: `docker-up` **13.05 s**, `up` **217.99 s**,
`test` **43.52 s**, `down` **0.93 s**, `docker-down` **2.41 s**. All **296 archived
evidence files** remained byte-identical, including both benchmark outputs and
all 13 fault histories/proofs. Cleanup left zero owned containers, volumes,
networks or build tags; all ten unrelated volume identities stayed unchanged.

### Current single-node baseline

Measured **2026-11-17** on the same host/corpus with **1 server × 2 CPUs / 2 GiB**
and tool **2 CPUs / 3 GiB**. Fresh infrastructure/setup, full metadata/vector proof,
correctness, retained-volume reopen and cleanup passed. Setup **116.78 s**;
reopen **106.85 s**; 100,000 documents / 38.4M coordinates matched, with 450
queries / 100 tenants and mean dense recall@10 **0.990**. The production sequence
checks correctness before and after reopen; it does not imply single-node HA.
Cleanup left zero owned resources/tags and all ten unrelated volume identities
unchanged. `docker-up` **7.37 s**, `down` **0.64 s**, `docker-down` **1.88 s**.

One warm benchmark after reopen: 16 warmups + 1,000 timed requests per case,
**6,000 requests / 0 failures**, with precomputed vectors and no encoding cost.

| Mode | Clients | QPS | p50 ms | p99 ms | Dense recall@10 |
|---|---:|---:|---:|---:|---:|
| lexical | 1 | 166.26 | 1.89 | 43.08 | — |
| lexical | 8 | 228.85 | 3.61 | 213.52 | — |
| dense | 1 | 359.14 | 2.61 | 3.38 | 0.9890 |
| dense | 8 | 794.44 | 6.23 | 42.39 | 0.9890 |
| hybrid | 1 | 122.46 | 3.92 | 46.17 | — |
| hybrid | 8 | 172.67 | 7.53 | 223.10 | — |

### Current retained-cluster benchmark

Measured **2026-11-17** after the four read-lag-10 role/disk cases, with the
187,010-ACK history retained and verified before timing. Apple M4 Pro, native
aarch64 Docker VM **11 CPUs / 24.42 GiB**; **3 servers × 2 CPUs / 2 GiB**,
benchmark tool **2 CPUs / 3 GiB**. CPU caps are ceilings on a shared host.
Same 100,000 product texts/vectors and 1,000 held-out queries; only inventory
metadata changed. One warm run, **16 warmups + 1,000 timed requests per case**:
**6,000 timed requests, 0 failures**, no resource-sampling errors. Encoding,
setup and pre-benchmark reconciliation are excluded. No cold-OS-cache or
confidence-interval claim; the fault workload is not a capacity benchmark.

| Mode | Clients | QPS | p50 ms | p99 ms | Dense recall@10 |
|---|---:|---:|---:|---:|---:|
| lexical | 1 | 152.86 | 2.13 | 46.34 | — |
| lexical | 8 | 776.17 | 2.89 | 68.50 | — |
| dense | 1 | 307.23 | 3.06 | 4.28 | 0.9880 |
| dense | 8 | 1,085.56 | 6.75 | 12.24 | 0.9879 |
| hybrid | 1 | 112.93 | 4.27 | 47.17 | — |
| hybrid | 8 | 577.16 | 5.77 | 78.91 | — |

Candidate nDCG@10: lexical **0.3192**, dense **0.5702**, hybrid **0.6028**;
100 scorable queries per mode, 0 zero-IDCG queries. The historical results below
remain separate; topology, resource budgets and ranking semantics must be matched
before comparing systems.

### Readiness and recovery behavior

**Readiness tradeoff:** Typesense 30.2 defaults `healthy-read-lag` to 1,000.
Its Raft `on_apply` queues asynchronous index work; `/status`'s committed index
is not an applied-index watermark. During the failed run, the restarted node
was read-ready with 839 queued writes. No snapshot existed yet; native recovery
recreated its derived DB from the retained Raft log. The failed search was kept
as a failure, and its acknowledged updates were included in subsequent checks.
This observation is not a demonstrated upstream corruption bug. Search response assembly can skip
a hit after a storage fetch error (`collection.cpp`); this is a source-level
hypothesis for short results, not a confirmed explanation of this run.

The passed leader run's largest successful-completion gaps were **32.60 s for
ACKs**, **46.58 s for searches** and **46.58 s for GETs**. During repair,
successful write-request p50/p99 were **9.42/57.21 ms**; successful search
p50/p99 **38.76/603.89 ms**. These are rate-limited fault observations with
read failover included, not capacity measurements. Raw reports separate failed
requests and masked failed attempts from successful latency samples.

The initial stricter runs explicitly set `HEALTHY_READ_LAG=0`, requiring stricter native read
readiness. Under continuing writes, followers sometimes returned 503 even with
zero queued writes while their committed index trailed the leader. The passed
leader case took over 11 minutes from repair start to the after phase. This
setting trades availability for stricter readiness; it does **not** establish
linearizable searches or reads (two stale GETs were observed). Search cardinality,
full payload and ACK reconciliation requirements have not been weakened.

The retained-volume rollout and all four cluster role/disk cases with read lag
**10** passed.
It uses the same writer load and unchanged search/body oracles. Make/Compose now
use **10 as the default**; the extended production matrix remains incomplete. The journal-ordering defect is fixed and has a regression test.

Operational commands (run serially, with the same `MODE` and `RESULTS_DIR`):

- `make failover`: discover and SIGKILL the actual leader.
- `make follower-crash`: independently fail a native follower.
- `make disk-failure`: fence a follower, permanently remove its owned volume,
  attach an explicitly labeled empty replacement, then verify every copy.
- `make rebuilt-crash`: crash the previously rebuilt member with its replacement
  storage retained; requires a successful replacement in that shard.
- `make cold-reopen`: stop/start the whole native stack on retained volumes;
  checks persistence, with warm host caches and no availability claim.
- `make verify` / `make report`: reconcile all histories, then report from raw
  journals. Reports reject stale fixture/history/copy proofs and retain failed cases.
- `make test-production`: serial core suite; replicated modes exercise each role,
  sharded mode repeats for all three groups, single mode checks cold reopen.
  Its existence is not proof it ran.

If a replacement is interrupted **after the labeled empty volume exists**, use
`RECOVERY_CASE=results/production/cluster/fault-disk-... make recover-disk`.
This helper validates its saved UUID, fixture, images and untouched volumes,
then reconciles all history before rebinding storage identities. It never deletes
or retries application mutations, and does not turn the failed case into a pass.
The helper also passed a reentrant check against the completed replacement plan,
including full read-only reconciliation. This does not qualify interruption at every
provisioning boundary. A missing replacement volume requires diagnosis/manual provisioning against the
saved plan; never rerun disk deletion as a recovery shortcut.

`retrieval-corpus` links to tracked `datasets/esci` source; `benchmark.py` links to
its tracked benchmark controller. `prepare_build.py` materializes only source
into this example's ignored `.build/` (regular hardlinks, copy fallback). Docker
builds from those local contexts. `corpus.py` downloads/verifies inputs into
`results/corpus/`; no sibling Makefile or generated state is used. Python remains
for controllers, ONNX/NumPy embedding/reference generation and the existing
retrieval-quality benchmark; the live application is Go stdlib with Go modules,
formatter, race tests and vet. No generated build files/results are committed.

If `make cold-reopen` exhausts its health budget while native replay continues,
keep the volumes and run `make verify` to wait for native readiness and reconcile
the complete retained history. The original failed case stays failed.

The whole-stack reopen failed its Compose health budget; it is not a passed
bounded persistence result. Its independent post-failure reconciliation passed
with all 187,010 ACKs: 300,000 physical documents / 115.2M coordinates. The
original containers, images and volumes were retained with new native processes.
Final proof completed **619.78 s after before-state capture**, including diagnosis,
readiness wait and full readback; this is an upper bound, not isolated recovery time.
Native logs confirm no snapshot existed at reopen. The default snapshot interval
is 3,600 s and its timer starts at process creation, so these frequent lab
restarts keep replaying the growing Raft history. Snapshot-cadence/recovery-cost
experiments remain unrun; these timings are not steady-state production RTOs.

The fresh default five-command lifecycle passed: `docker-up` **12.41 s**,
`up` **301.02 s**, `test` **49.02 s**, `down` **1.68 s**, `docker-down` **2.00 s**.
It verified 300,000 physical documents / 115.2M coordinates and 450 queries over
100 tenants (mean dense recall@10 **0.990**). All **158 archived evidence files**
matched their pre-teardown SHA-256 hashes. Both cleanups left zero owned
containers/volumes/networks/build tags; all ten unrelated volume identities
remained unchanged. Retained `up`/`test`/report also passed without reloading.

Remaining qualification: a passing bounded whole-stack reopen for the retained
three-node history, and the extended cases
below. The failed probe budget and eventual data recovery remain above. Extended tests are also **unrun**: peer partitions/minority behavior,
Raft membership scaling, bounded pressure/I/O errors, upgrades and independent
backup restore. Permanent volume loss is different from injected physical I/O
failure. All replicas still share one Docker VM.

Inspected pinned references: [30.2 document/import API](https://typesense.org/docs/30.2/api/documents.html),
[server configuration](https://typesense.org/docs/30.2/api/server-configuration.html),
[Raft apply/recovery](https://github.com/typesense/typesense/blob/v30.2/src/raft_server.cpp),
[asynchronous index queue](https://github.com/typesense/typesense/blob/v30.2/src/batched_indexer.cpp),
[local API dispatch](https://github.com/typesense/typesense/blob/v30.2/src/core_api.cpp),
[search response assembly](https://github.com/typesense/typesense/blob/v30.2/src/collection.cpp).

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
versions after rejoin. The table above covers shard 0. Independent leader
crashes were also run on shards 1 and 2, each after
reloading and verifying the 100,000-product catalog. All nine physical copies
were reconciled after every crash; the eight surviving server containers kept
the same container IDs and start times.

| Shard | Killed elected leader | ACKed unique IDs | Lost ACK versions | Batches ACKed while absent | Global searches while absent | Failed searches while absent | Failed-attempt to resumed ACK, s |
|---|---|---:|---:|---:|---:|---:|---:|
| 0 | ts3 | 2,070 | 0 | 154 | 175 | 0 | 6.40 |
| 1 | ts6 | 4,800 | 0 | 139 | 142 | 0 | 8.34 |
| 2 | ts7 | 4,190 | 0 | 164 | 166 | 0 | 4.16 |


Ambiguous/failed update batches (shard 0: **21**; shard 1: **3**; shard 2: **71**) were journaled
without blind replay. Quorum loss, disk failure and network partitions were not
simulated.

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

The fault runner refuses an existing `churn.jsonl`, `churn.json`, `fault.json` or
`reconciliation.json`; it never deletes an earlier ACK witness. Preserve and
diagnose a failed fixture before archiving its evidence and starting a separate
clean lifecycle. This guard does not add cross-run version reconciliation or an
independent reader to the existing churn loop.
