# Vespa phased product search and recovery

ESCI product search with lexical, dense, hybrid and global reranking profiles.
The production-test backfill adds independent Go inventory writers/readers,
preserved version histories, native replica-directed full metadata/vector checks
and separate crash, disk-replacement and cold-reopen cases.

## Quick start

1. `make docker-up` — verify this example's own corpus, build local workload images and start Vespa hosts.
2. `make up` — deploy/feed a fresh application, or verify an already-bound retained fixture without reloading it.
3. `make test` — run controller controls and independent traffic through full replica/correctness checks.
4. `make down` — remove the native application after a passing reconciliation; keep supporting hosts.
5. `make docker-down` — remove only this project's hosts, volumes and built image tags; archive evidence.

Requires Docker Compose and host Python 3.11+. Native images are pinned to Vespa
**8.781.11**; no host Go/Python application dependencies are required. The host
controller uses the standard library. The Python scientific corpus/ANN verifier
is a deliberate exception to the Go/Rust/C++ application preference; live traffic
is Go with standard-library HTTP/JSON, built and tested in Docker.

The first corpus preparation downloads public ESCI data and the pinned embedding
model, then builds 100,000 products, 1,000 queries and 384-dimensional float32
vectors. Inputs, model cache and generated outputs live under this example's own
ignored `results/corpus/`; no sibling Makefile or generated fixture is used.
`retrieval-corpus/`, `benchmark.py`, `version_history.py` and three generic Go
source files are repository-tracked source links. `prepare_build.py` materializes
regular hard links/copies into ignored `.build/` contexts before Docker builds.

The fixture has **100,000 distinct product IDs / 100 synthetic tenants**;
string metadata includes source ID, locale, name, description, brand and color,
with integer tenant, stock, price and version fields. It is seeded at **20261115**,
with **912–1,078 products per tenant**, **47,043 empty descriptions** and
description text capped at 4,000 characters. Prepared product JSONL is
**75,379,504 bytes (71.89 MiB)** and product float32 vectors are
**153,600,000 bytes (146.48 MiB)**. The 1,000-query JSONL and vectors occupy
**343,768 / 1,536,000 bytes**. These are input sizes, excluding model/cache,
indexes, journals and native exports; artifact/model hashes live in the corpus
manifest. See the [corpus selection and labels](../../datasets/esci/README.md)
for sampling limits and the lack of exhaustive relevance judgments.

Full three-copy proof exports currently occupy roughly **2.4 GiB per case**
across its live/final audits. Evidence accumulates across failed and successful
runs and survives teardown. Provision space for the intended case count;
the controller also requires an **8-GiB reserve plus its conservative next-export
estimate** and refuses to delete old evidence to fit a new test.

## Deployment profiles

| MODE | Native layout | Per-host limits | Whole-SUT ceilings |
|---|---|---|---|
| `single` | one combined host, one copy | 2 CPUs / 4 GiB | 2 CPUs / 4 GiB |
| `cluster` (default) | three combined config/controller/query/content hosts; three copies | 2 CPUs / 4 GiB | 6 CPUs / 12 GiB |
| `sharded` | three config/controller/query hosts; nine content hosts in three groups of three | controls: 2 CPUs / 2 GiB; content: 1 CPU / 1.5 GiB | 15 CPUs / 19.5 GiB |

Sharded mode uses **native bucket distribution**, one copy per replica group:
each group splits the complete catalog across its three content hosts. It is not
customer-modulo routing. Flat three-node mode stores every document on every
content host. Grouped mode sets `min-node-ratio-per-group=1` so an incomplete
query group leaves service. Hostnames/network are private, with no published
ports. All replicas share one Docker VM; these are not independent failure domains.

The tools service has 2 CPUs / 3 GiB, and each independent traffic role has 1 CPU /
512 MiB. Sharded content hosts explicitly bypass the image's 4-GiB startup floor;
cgroup limits and native feed memory/disk guards remain enabled. These are lab
heaps, not production sizing guidance. Never run another database benchmark or
corpus encoding concurrently. A mode owns its infrastructure until full teardown.

## Operational targets and evidence

```sh
make failover                           # discover/crash the active controller host in flat mode
make replica-crash                      # crash a different owned content host
make disk-failure                       # fence one owner; replace its entire named /opt/vespa/var volume
make rebuilt-crash                      # fail a different member and read a fresh ACK from the rebuilt member
make test-production                    # serial applicable matrix; not yet fully qualified
make cold-reopen                        # stop/start the complete stack with retained volumes
make benchmark                          # only after a passing recovery/correctness report
make status
```

`MODE=single|cluster|sharded`, `DOCUMENTS=100000`, `QUERIES=1000`,
`DURATION_SECONDS=1800`, `UPDATE_BATCH=10`, `UPDATE_CONCURRENCY=4` and
`NODE_DOWN_SECONDS=180` (flat) / `60` (sharded) are Make variables. The update
batch is a bounded client group of per-document requests, not a native bulk API.
`FAULT_SERVICE=node1` or an owned content host selects an explicit target; native
role discovery is the default. Single-node mode rejects HA/disk-loss claims.

Each case gets exclusive fsynced writer and reader journals, immutable starting
history and timestamped phases. New cases extend all earlier intents/ACKs,
including failed attempts; no update replay, table reset or journal overwrite is
used to pass reconciliation. The writer has conditional identity/version guards,
3-second server deadlines, bounded concurrency and **no mutation retry**. Timeout
outcomes remain ambiguous. The journal's `rejected` stage means an HTTP 412
conditional response, **not proof that no replica applied the update**; all
attempted versions remain eligible non-ACK outcomes during reconciliation.
Read endpoint failover is recorded separately from
whole-operation errors. The generator stays live through fault injection,
native repair and a complete copy audit. The writer then stops, the independent
reader stays live through a second all-ACK audit and query correctness check.

Native recovery requires every storage/distributor up, each content copy's
expected total/ready count, empty reported merge queues and a zero cluster
out-of-sync ratio. A missing metric is **unknown**, not zero. The pinned controller
omits its ratio until all distributors have reported. Infrastructure health alone
is insufficient. The full audit reads every ID directly from every content node
using `vespa-get --replica-override`, verifies each replica group's coverage,
all metadata and **all 384 float32 coordinates**. It is native API readback, not
an independent decoder of on-disk files.

The native audit CLI retains its supported **read-only routing retries**: a new
session can initially receive WRONG_DISTRIBUTION before learning the current
routing state. The CLI does not expose an internal retry count; exports are not
first-attempt availability measurements. Per-request/overall timeouts and strict
reply count/identity parsing remain enforced. UTF-8 output is explicit. Compressed
exports, stderr, fixed ACK floors and proof summaries remain in `results/production/<mode>/cases/`.

Vespa omits empty-string fields from document JSON. Only an omission whose
expected fixture value is exactly an empty string is canonicalized; missing
nonempty/numeric fields fail. Search can overlap attribute updates under Vespa's
best-effort semantics. Mixed search fields are recorded only when their known
values correspond to an overlapping attempted update; atomic document GETs and
post-repair complete copy audits retain strict full-value checks. Stale and
partial observations remain separate counters, not successful linearizability claims.

Disk replacement verifies exact project labels, the stopped old container's
identity and successful daemon-level absence before removing the owned volume.
It records changed volume birth identity, independently checks the replacement
is empty, and verifies the rebuilt copy. This is **permanent empty-volume loss**,
not physical EIO, bit rot or power failure; a combined host also loses one local
config-server/ZooKeeper copy. Unknown Docker state blocks destruction. Failed
repair may require manual continuation from `disk-transition.json`; no automatic
reset makes the failed report pass.

## Observed conditional-update gotcha

During node1's outage, two single-submission conditional updates returned HTTP
**412 / TEST_AND_SET_CONDITION_FAILED**. Final native exports nevertheless
contained both exact attempted bodies at version **4256** (IDs **42551, 42552**),
and all three physical copies agreed. Neither operation is counted as an ACK.
The pinned persistence tracker explicitly handles partial test-and-set failures
and changes the overall result to success only when its majority-success rule is
met. This is evidence that an error response cannot be treated as proof of no
side effects; it does not establish the exact internal sequence for these two
requests. The controller never retried these mutations or reset their state.

[Inspected partial-TaS reply handling](https://github.com/vespa-engine/vespa/blob/v8.781.11/storage/src/vespa/storage/distributor/persistencemessagetracker.cpp).
The strict version-less-than guard is not a general idempotency mechanism for
caller retries. A same-version/body idempotent conditional form is a future
experiment, not a tested guarantee in this example.

## Current backfill status — 2026-11-18

**Flat core/lifecycle verified; not a complete production matrix.**
Fresh flat-cluster loading took **397.09 s** for **100,000 documents**
(**251.83 documents/s**, deployment/feed/index/initial logical verification).
This was setup timing, not an isolated ingestion capacity benchmark.

- Native-format/shared contract/corruption controls: **9 tests passed**, including
  wrong vector coordinates, metadata, missing copies and unknown versions.
- Host controller controls: **11 tests passed**, including unknown runtime/owner
  fencing, active-controller discovery, missing/lagging repair metrics and rejection
  of benchmarking after an incomplete newest case.
- Go workload `go test -race` and image-build tests/vet passed.
- A retained-fixture correctness case passed **7,090 new ACK updates**, **4,454
  searches and 4,454 document GETs**, zero observed stale/wrong/failed operations;
  both native audits checked **300,000 copies / 115.2 million coordinates**.
- The initial native export used `--noretry` and omitted one failed routing reply
  per node. Strict parsing rejected it. Its **3,800 ACKs** remain in the cross-case
  history; the original case stays failed. Corrected native read-only retry
  behavior was verified without resetting the fixture.
- The first controller-crash attempt restarted the host but failed its harness
  gate on an optional missing metric. Corrected-gate reconciliation passed without
  reset. A second crash attempt completed native audits and replica version
  agreement, then failed report generation on a latency-free partial-search
  diagnostic. Both remain failed runs with their ACKs/proofs preserved; the
  reporter now separates diagnostic counts from API latency samples.
- Before the next destructive case, the strict final proof covered **38,106 ACK
  updates / 41,710 attempted updates**, with **3,604 ambiguous outcomes** across
  five histories. Final native versions agreed on every copy; **414 physical
  copies** had an allowed version above their ACK floor (138 document IDs,
  three copies each). Those are ambiguous outcomes, not acknowledged data loss.
- Empty node1 volume replacement passed: **15,443 new ACKs / 17,860 attempts**,
  2,415 ambiguous and two HTTP 412 outcomes; **7,680 searches / 7,680 GETs**,
  six failed read operations and no observed wrong/stale bodies. Both full-copy
  audits passed; final versions agreed on all three copies.
- The next case kept rebuilt node1 online while active-controller host node0 was
  absent. A directed native read from node1 verified a fresh ACK at **version
  6417**, before node0 returned. The case passed **14,542 new ACKs / 16,470
  attempts**, 1,928 ambiguous outcomes, 7,084 searches and 7,081 GETs. Seven
  read operations failed; one stale search hit was observed, with no stale/wrong
  document GETs. All **26 unrelated native daemon/supervisor identities** stayed
  unchanged. Final copy versions agreed after complete metadata/vector checks.
- Native survivor witnesses capture **41 daemon/supervisor identities** across
  the three hosts.
- Whole-stack retained reopen passed **9,134 ACK updates / 13,160 attempts**,
  with 4,026 ambiguous outcomes; **4,964 searches / 4,963 GETs** included 790
  search errors and 791 GET errors during the outage, with no observed wrong or
  stale bodies. All three container/image/volume identities were retained and
  final native copy versions agreed. The 20-second stop deadline produced exit
  **137** on node1 (not OOM), versus exit **0** on node0/node2: this is a mixed
  graceful/forced process stop, not a clean shutdown or physical power failure.
- Across the eight preserved histories: **77,225 ACK updates / 89,200 attempts**,
  **11,973 ambiguous outcomes / two conditional responses**, five passed cases
  and three failed cases. The final full native audit reconciled all acknowledged
  bodies/vectors with **zero detected acknowledged loss**. Failed runs retain
  their original status; their successful native recovery does not make their
  harness failures pass.
- A separate fresh-volume recreation loaded the same corpus in **385.98 s**
  (**259.08 documents/s**, setup scope above). Its baseline passed **6,420 ACKs**,
  **3,988 searches / 3,988 GETs**, zero observed errors/stale/wrong values, and
  both full 300,000-copy / 115.2M-coordinate audits.
- The fresh fixture's non-controller node1 crash passed **18,877 ACKs / 22,590
  attempts**, with **3,713 ambiguous outcomes**. It kept **8,821 searches / 8,821
  GETs** running with zero failed operations or wrong/stale document GETs; **nine
  stale search hits** were observed. Read endpoint failover made **1,973 additional
  attempts**, all in ultimately successful operations: zero failed operations
  does not mean zero failed attempts. There were **6,553 new post-fence ACKs** while
  node1 remained absent; the first arrived **12.15 s** after fencing. Longest ACK /
  search / GET completion gaps were **12.38 / 7.90 / 7.90 s**. All **28 unrelated
  daemon/supervisor identities** stayed unchanged. Final full-copy versions agreed;
  24 physical copies (eight IDs) held allowed non-ACK versions above the ACK floor.
  The retained-proof guard was exercised before injection. The separate final
  `make test` passed another **6,860 ACKs**, **4,207 searches / 4,207 GETs**, with
  zero failed/stale/wrong observations and both full-copy audits. Across this
  fresh fixture's three cases: **32,157 ACKs / 35,870 attempts**, 3,713 ambiguous
  outcomes, all final copies agreeing and **zero detected acknowledged loss**.
- Application teardown and infrastructure teardown passed. Archiving verified
  **526 files** against their pre-move SHA-256 digests, preserving failed evidence
  and journals. The fresh fixture completed the full five-command lifecycle plus
  the replica-crash case; its archive verified another **191 files**. Cleanup left
  **zero owned containers, volumes, networks or built image tags**, and all **ten
  recorded foreign volumes** retained their birth identities. Raw teardown proof:
  `results/teardown-check-aa51ed0921f94eb4b8bf6e9d4f027245.json`.

### Live fault observations

Rate-limited traffic used four concurrent conditional-write workers, groups of
ten individual updates and a 100-ms think interval. These are fault observations,
not capacity measurements. Latency cells are **successful-request p50 / p99 ms**;
failed requests are counted separately so fast rejection cannot improve them.
“Outage + repair” spans the fault marker through native repair completion, before
the live full-copy audit. Before/after windows are approximately 15 seconds each.

| Case | Phase | ACK/s | Write ms | Search ms | GET ms | Non-ACK writes / failed reads |
|---|---|---:|---:|---:|---:|---:|
| Disk | Before | 46.46 | 33.77 / 46.54 | 4.71 / 19.45 | 1.83 / 3.06 | 0 / 0 |
| Disk | Outage + repair | 28.58 | 32.75 / 208.04 | 5.40 / 51.55 | 1.69 / 28.70 | 2417 / 6 |
| Disk | After | 48.85 | 33.08 / 47.18 | 4.19 / 10.84 | 1.38 / 4.10 | 0 / 0 |
| Rebuilt peer | Before | 46.61 | 35.18 / 45.98 | 4.58 / 13.64 | 1.49 / 3.09 | 0 / 0 |
| Rebuilt peer | Outage + repair | 25.79 | 32.74 / 98.00 | 4.92 / 52.29 | 1.76 / 45.91 | 1928 / 7 |
| Rebuilt peer | After | 48.61 | 33.06 / 45.18 | 4.35 / 13.61 | 1.72 / 7.88 | 0 / 0 |
| Cold reopen | Before | 47.49 | 33.11 / 45.31 | 4.20 / 19.98 | 1.80 / 3.06 | 0 / 0 |
| Cold reopen | Outage + repair | 23.38 | 34.20 / 53.02 | 5.29 / 18.96 | 2.13 / 10.38 | 4026 / 1581 |
| Cold reopen | After | 48.03 | 33.21 / 44.52 | 4.29 / 12.70 | 1.72 / 3.84 | 0 / 0 |

Longest successful-completion gaps (ACK / search / GET) were **81.97 / 20.37 /
40.24 seconds** for disk replacement, **118.09 / 20.24 / 40.25 seconds** for
rebuilt-peer loss, and **55.41 / 55.34 / 55.49 seconds** for cold reopen.
These include in-flight and client routing delays, not just native election time.
From the restart marker, the native repair gate was satisfied after **128.48 /
128.09 / 77.31 seconds**, respectively; the complete live-copy proof finished
after **233.65 / 231.75 / 180.23 seconds**. These are observed detection times,
including Docker start, two-second polling plus inspection time and native metric
publication delay; they do not isolate election or internal catch-up duration.
Raw reports retain phase durations, operation counts, successful/failed latency
distributions and additional read attempts. Samples from short windows do not
establish stable tail behavior. API latency excludes local witness fsync.

### Post-recovery warm-query benchmark

One run on an **Apple M4 Pro / ARM64**, Docker VM **11 CPUs / 24.42 GiB**,
three native Vespa hosts at **2 CPUs / 4 GiB each**; tools at **2 CPUs / 3 GiB**.
Each row timed **1,000 requests** after **16 warmups** using precomputed vectors;
all **8,000 requests succeeded**. This measures warm HTTP request throughput and
latency, excluding embedding generation and setup; it is neither a cold-cache
measurement nor a three-trial comparative claim.
Dense recall@10 was **0.9977** against the exact-vector reference in both client
configurations. Candidate-only nDCG@10 on 100 labeled queries was **0.5485 /
0.5702 / 0.5918 / 0.5892** for lexical/dense/hybrid/rerank; the global reranker
again provided no quality gain over hybrid on this sample.

| Search | Clients | Queries/s | p50 ms | p99 ms |
|---|---:|---:|---:|---:|
| Lexical | 1 | 378.63 | 2.51 | 4.82 |
| Lexical | 8 | 1,056.68 | 6.37 | 30.84 |
| Dense | 1 | 124.48 | 7.76 | 10.22 |
| Dense | 8 | 303.41 | 21.16 | 61.77 |
| Hybrid | 1 | 114.53 | 8.64 | 12.23 |
| Hybrid | 8 | 264.54 | 23.46 | 70.78 |
| Global rerank | 1 | 114.28 | 8.67 | 12.10 |
| Global rerank | 8 | 263.76 | 22.77 | 67.70 |

Nine Docker-stat samples at roughly **5.04 seconds** observed per-host maximum
CPU utilization **207.14% / 196.50% / 205.32%** (100% denotes one core), and
memory usage **2.227 / 2.119 / 2.052 GiB**. These are sampled Docker counters,
not integrated process CPU core-seconds, peak RSS or cgroup peak measurements;
short peaks can be missed. No sampling errors occurred. Raw latency samples,
resource samples and benchmark source are preserved under
`results/production/cluster/previous-runs/94bb0099a92d4636bfebe6231e95437b/benchmarks/d5abf5b4a848463e8d0584fe8a89ffa8/`.

Remaining qualification: the complete serial `test-production` target, repeated
retained `make up`, and reruns of single/sharded profiles with the new controller. Partitions/quorum loss, scaling/resource changes, upgrades, bounded
pressure/EIO and independent backup restore remain unrun in this backfill.
Historical results below apply to their original harness, not these new gates.

## Directory structure

```text
application/{schemas/product.sd,generated/}
inventory-workload/{Dockerfile,go.mod,*.go}
app/{Dockerfile,client.py,native.py,test_native.py,test_replica_audit.py,requirements.txt}
{control.py,corpus.py,prepare_build.py,replica_audit.py,search_checks.py,metrics.py,test_controls.py}
{retrieval-corpus,benchmark.py,version_history.py}   # tracked source links
{Makefile,docker-compose.yml}
.build/                                           # ignored local build contexts
results/{corpus,production/<mode>}/                # ignored inputs/evidence
```

## Native contracts inspected

[Document JSON empty-string representation](https://docs.vespa.ai/en/reference/schemas/document-json-format.html),
[best-effort consistency](https://docs.vespa.ai/en/content/consistency.html),
[pinned node-directed get filtering](https://github.com/vespa-engine/vespa/blob/v8.781.11/storage/src/vespa/storage/distributor/operations/external/getoperation.cpp),
[native CLI synchronous replies and routing](https://github.com/vespa-engine/vespa/blob/v8.781.11/vespaclient-java/src/main/java/com/yahoo/vespaget/DocumentRetriever.java),
[pinned optional controller metric](https://github.com/vespa-engine/vespa/blob/v8.781.11/clustercontroller-core/src/main/java/com/yahoo/vespa/clustercontroller/core/restapiv2/requests/ClusterStateRequest.java),
[native merge metrics](https://docs.vespa.ai/en/reference/operations/metrics/vespa-metric-set.html).

## Historical 2026-11-15 benchmarks

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
