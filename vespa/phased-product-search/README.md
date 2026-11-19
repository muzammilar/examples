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
Resource-counter collection requires a Linux Docker daemon with readable cgroup
v2 `cpu.stat`, `memory.current`, `memory.peak` and `memory.stat` files inside the
native containers. The measured Docker Engine **29.5.3** setup provides these interfaces;
counter collection fails closed when these interfaces are unavailable.

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
make controller-crash                   # native active controller; 180-second default hold
make replica-crash                      # another content host; group 2 in sharded mode
make disk-failure                       # fence one owner; replace its entire named /opt/vespa/var volume
make rebuilt-crash                      # fail a different member and read a fresh ACK from the rebuilt member
make test-production                    # serial applicable matrix; not yet fully qualified
make cold-reopen                        # stop/start the complete stack with retained volumes
make crash-reopen                       # SIGKILL the complete stack; restart retained storage
make benchmark                          # only after a passing recovery/correctness report
make status
```

`MODE=single|cluster|sharded`, `DOCUMENTS=100000`, `QUERIES=1000`,
`DURATION_SECONDS=1800`, `UPDATE_BATCH=10`, `UPDATE_CONCURRENCY=4` and
`NODE_DOWN_SECONDS=180` (flat) / `60` (sharded) are Make variables. The update
batch is a bounded client group of per-document requests, not a native bulk API.
`FAULT_SERVICE=node1` or an owned content host selects an explicit target; native
role discovery is the default. Single-node mode rejects HA/disk-loss claims.

`CONTROLLER_DOWN_SECONDS=180` controls the separate controller-host hold. Sharded
mode defaults to content0 for `failover`, content8 for `replica-crash` and content3
for `disk-failure`, exercising all three replica groups across the matrix. Its
serial suite also crashes the active control/query/config host. A rebuilt-member
read checks a bounded set of fresh ACKs for a key physically stored on that
member; it never assumes an arbitrary ACK belongs to that member's shard. The
other failed peer must remain positively fenced throughout this directed read.

`crash-reopen` tests an explicit whole-stack process crash, with expected
unavailability while every native host is stopped. It requires exit 137 without
an OOM flag, retains the exact containers/images/volumes and reconciles all prior
ACKs after restart. This differs from `cold-reopen`'s bounded graceful stop and
from physical power loss or EIO. The single-node production sequence includes
both reopen paths; a one-copy database has no HA or empty-disk recovery claim.

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

Remaining qualification: the complete flat-cluster serial `test-production`
target/retained setup. The expanded single-node aggregate subsequently passed
on 2026-11-19, as recorded below. The sharded core matrix and retained setup
also passed. Partitions/quorum loss, scaling/resource changes, upgrades,
bounded pressure/EIO and independent backup restore remain unrun.
Historical results below apply to their original harness, not these new gates.

### Single-node follow-up — 2026-11-18

Executed serially: fresh baseline, correctness, bounded graceful reopen,
correctness, whole-stack SIGKILL reopen, then retained `make up`. The latter
verified the existing fixture without deployment/feed/reset. **Six cases passed**:
**37,308 ACKs / 44,280 attempts**, **6,972 ambiguous outcomes**, zero detected
acknowledged loss. Each live/final proof checked **100,000 bodies / 38.4 million
float32 coordinates**. All final versions matched their retained witnesses.
The later Makefile update includes SIGKILL in the single-node `test-production`
sequence; it was not replayed as one invocation in the 2026-11-18 run.
The 2026-11-19 follow-up below closes that invocation gap.

| Case | ACKs | Attempts | Ambiguous | Failed search / GET | Longest ACK / search / GET gap, seconds |
|---|---:|---:|---:|---:|---:|
| Baseline | 5,320 | 5,320 | 0 | 0 / 0 | 0.52 / 0.53 / 0.53 |
| Correctness | 5,230 | 5,230 | 0 | 0 / 0 | 0.50 / 0.49 / 0.49 |
| Graceful reopen | 8,138 | 11,380 | 3,242 | 777 / 778 | 44.98 / 44.66 / 44.89 |
| Correctness | 5,280 | 5,280 | 0 | 0 / 0 | 0.53 / 0.53 / 0.54 |
| SIGKILL reopen | 8,070 | 11,800 | 3,730 | 566 / 566 | 41.59 / 41.22 / 41.49 |
| Retained setup | 5,270 | 5,270 | 0 | 0 / 0 | 0.52 / 0.53 / 0.53 |

The graceful stop exited **0**, the SIGKILL stop **137**; neither had an OOM flag.
No wrong/stale GETs or stale/partial search hits were observed in these cases.
Unavailability during complete shutdown is expected; these are one-copy process
recovery tests, not HA, independent-host or physical power-loss guarantees.
Initial deploy/feed/index/logical verification took **365.95 s / 273.26 docs/s**;
its CPU cost was not measured. All five lifecycle commands completed. Teardown
hash-verified **323 files**, left zero owned infrastructure/built tags and retained
the identities of all ten recorded foreign volumes.

One post-recovery warm benchmark used the same host/corpus as the flat results,
one native host at **2 CPUs / 4 GiB**, tools at **2 CPUs / 3 GiB**, 16 warmups and
1,000 timed requests per row. All **8,000 requests succeeded**; dense recall@10
was **0.9918**. This is one trial with different topology, durability and budgets,
not a normalized cross-profile scaling comparison or a cold-cache result.

| Search | Clients | Queries/s | p50 ms | p99 ms |
|---|---:|---:|---:|---:|
| Lexical | 1 | 612.78 | 1.57 | 3.19 |
| Lexical | 8 | 1,137.87 | 4.56 | 46.79 |
| Dense | 1 | 331.56 | 2.80 | 4.14 |
| Dense | 8 | 602.85 | 7.34 | 59.11 |
| Hybrid | 1 | 278.58 | 3.43 | 7.25 |
| Hybrid | 8 | 460.41 | 7.06 | 74.34 |
| Global rerank | 1 | 274.89 | 3.41 | 7.41 |
| Global rerank | 8 | 451.06 | 7.04 | 77.00 |

Native cgroup CPU delta was **29.52 core-seconds** (**27.52 user / 2.00 system**)
over **22.78 s** around the entire benchmark command, including warmups, quality,
setup and native housekeeping. It is not a sum of the timed-query windows; the
workload container's CPU is excluded. Afterward, charged memory was
**3,549,163,520 bytes** and the current cgroup's lifetime peak
**3,701,084,160 bytes**, including recovery/retained setup before the benchmark.
These include file cache/kernel charges and are neither process RSS nor a
benchmark-only memory peak. Raw CPU/memory counters, Docker samples, stable
native process/container checks and measurement source are retained alongside
the query samples. Nonzero request failures make the benchmark target fail.
The [kernel cgroup v2 interfaces](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)
define cumulative CPU microseconds and cgroup-lifetime memory peaks. Docker's
[stats CLI subtracts inactive file cache](https://docs.docker.com/reference/cli/docker/container/stats/)
on cgroup v2, so its displayed memory differs from the charged `memory.current`.

Evidence: `results/production/single/previous-runs/04afaa079eab4b388223143ce9869e3f/`;
benchmark `benchmarks/2e39b61832004f4280154102694f7443/` inside that archive.

Profile controls now isolate their topology from the caller's `MODE` and check
single-copy readiness, per-group sharded coverage, cgroup counter resets and
physical rebuilt-member ACK identity/body/version. **17 host controls** pass;
**10 container-side controls** include duplicate/missing IDs with matching shard
totals. These synthetic controls do not qualify a live sharded deployment.

### Single-node aggregate follow-up — 2026-11-19

Executed the exact `MODE=single make docker-up up test-production` invocation,
then `MODE=single make down docker-down`. All **five histories passed**:
**35,144 ACKs / 43,530 attempted rows**, **8,386 ambiguous attempts**, no rejected
updates and no detected acknowledged-version loss. Live and final native proofs
checked every one of the **100,000 metadata bodies and 38.4 million float32
coordinates** per export. No stale/wrong GETs, stale search hits or partial search
bodies were observed. Ambiguous responses remain non-ACKs even if their bodies
later appear.

| Case | ACKs | Attempts | Ambiguous | Failed search / GET | Longest ACK / search / GET gap, seconds |
|---|---:|---:|---:|---:|---:|
| Baseline | 6,380 | 6,380 | 0 | 0 / 0 | 0.54 / 0.55 / 0.56 |
| Correctness | 5,970 | 5,970 | 0 | 0 / 0 | 0.55 / 0.54 / 0.54 |
| Retained-volume whole-stack reopen | 9,044 | 13,690 | 4,646 | 741 / 742 | 50.87 / 50.86 / 50.99 |
| Whole-stack SIGKILL reopen | 8,260 | 12,000 | 3,740 | 723 / 723 | 41.80 / 41.82 / 41.89 |
| Final correctness | 5,490 | 5,490 | 0 | 0 / 0 | 0.52 / 0.53 / 0.53 |

These are offered-load recovery observations. ESCI encoding and later idle
PostgreSQL infrastructure shared the Docker VM during this follow-up; no new
capacity/comparative benchmark is claimed. Completion gaps include deliberate
outage holds and client pacing, not an isolated native election measurement.

Teardown archived and hash-verified **258 evidence files**, removed all owned
Vespa containers/volumes/networks and preserved all **23 pre-existing volume
identities**. The PostgreSQL project was left running. Earlier archives and failed
reports remain intact. Evidence:
`results/production/single/previous-runs/ec869129161548cb9d97c253955ec0d8/`.

### Sharded follow-up — 2026-11-18

The fresh 3-control / 9-content deployment completed its full serial
`test-production`, retained `make up`, benchmark and five-command lifecycle.
**Ten cases passed**, reconciling **139,501 ACK updates / 149,030 attempts** across
**99,969 distinct acknowledged IDs**, with **zero detected acknowledged loss**.
The **9,524 ambiguous outcomes / five HTTP 412 responses** remain non-ACK outcomes;
they are not assumed to have had no effect. No mutations were retried.

Each live/final audit performed **900,000 directed gets**: **300,000 present copies /
600,000 expected absences**, all metadata and **115.2 million float32 coordinates**.
Every replica group contained the complete 100,000-ID catalog exactly once; final
versions agreed across all three groups. Per-node baseline counts match the
historical placement table below. No wrong/stale GETs or stale/partial search hits
were observed. Read endpoint failover made **5,008 additional attempts** across the
run; successful operations must not be mistaken for zero first-attempt failures.

| Case | ACKs / attempts | Ambiguous / HTTP 412 | Failed search / GET | Longest ACK / search / GET gap, seconds |
|---|---:|---:|---:|---:|
| Baseline | 10,160 / 10,160 | 0 / 0 | 0 / 0 | 0.60 / 0.60 / 0.62 |
| Correctness | 10,170 / 10,170 | 0 / 0 | 0 / 0 | 0.54 / 0.54 / 0.54 |
| Control-host crash | 17,161 / 21,190 | 4,028 / 1 | 0 / 0 | 1.12 / 0.57 / 0.57 |
| Content0 crash | 17,199 / 17,210 | 11 / 0 | 0 / 0 | 3.13 / 15.22 / 15.22 |
| Content8 crash | 17,220 / 17,230 | 10 / 0 | 3 / 0 | 3.09 / 10.07 / 10.06 |
| Content3 disk replacement | 16,809 / 16,820 | 10 / 1 | 0 / 0 | 3.13 / 11.81 / 11.81 |
| Rebuilt-member peer loss | 17,430 / 17,440 | 7 / 3 | 0 / 0 | 3.10 / 9.24 / 9.24 |
| Whole-stack reopen | 12,762 / 18,220 | 5,458 / 0 | 695 / 696 | 63.42 / 63.15 / 63.43 |
| Correctness | 10,330 / 10,330 | 0 / 0 | 0 / 0 | 0.57 / 0.65 / 0.65 |
| Retained setup | 10,260 / 10,260 | 0 / 0 | 0 / 0 | 0.57 / 0.66 / 0.66 |

The control-host test fenced active node0; native controller node1 was observed
active during the 180-second hold, with **7,260 fresh post-fence ACKs** and a first
new ACK **0.96 s** after fencing. This observation does not isolate election time.
All **94 unrelated native daemon/supervisor identities** remained unchanged.
Content0/content8 crashes and content3 disk replacement exercised all three
replica groups. Data-worker cases preserved all **99 unrelated identities**.

Content3's owned volume was replaced after positive fencing; its creation time
changed from **03:13:51 to 04:04:57 UTC** and an independent empty-volume check
passed. After native rebuild/full-copy verification, a different member,
content0, was fenced. A directed read from content3 then returned fresh ACK
**ID 96288 / version 9629**, including the exact tenant/stock/price body, before
content0 returned. The bounded 256-candidate read verified actual physical
ownership instead of assuming that an arbitrary ACK belonged to content3.

Whole-stack stop retained all container/image/volume identities: eleven hosts
exited **0**, node1 exited **137** at the 20-second deadline, and none had an OOM
flag. This is mixed graceful/forced process shutdown with retained storage,
not physical power loss. Reads/writes stayed live through native repair and the
live-copy audit; the independent reader continued through final reconciliation.

The following rate-limited observations use **successful-request p50 / p99 ms**;
raw reports retain failed-request distributions/counts separately. Before/after
windows are roughly 15 seconds; outage + repair ends at the native repair gate,
before the live-copy audit. These are not capacity measurements or stable tail
estimates from long trials. API latency excludes local witness fsync.

| Case | Phase | ACK/s | Write ms | Search ms | GET ms |
|---|---|---:|---:|---:|---:|
| Controller | Before | 51.20 | 27.90 / 45.31 | 3.92 / 8.30 | 1.91 / 2.83 |
| Controller | Outage + repair | 40.18 | 26.69 / 44.52 | 4.31 / 12.38 | 1.88 / 9.58 |
| Controller | After | 52.27 | 29.03 / 45.18 | 3.72 / 9.30 | 1.81 / 3.25 |
| Disk | Before | 51.55 | 27.19 / 45.16 | 3.57 / 8.67 | 1.81 / 2.84 |
| Disk | Outage + repair | 45.29 | 25.31 / 186.38 | 3.22 / 7.72 | 1.56 / 22.68 |
| Disk | After | 52.66 | 27.16 / 45.43 | 3.41 / 8.67 | 1.62 / 12.48 |
| Reopen | Before | 51.73 | 27.13 / 44.80 | 3.38 / 7.61 | 1.77 / 3.45 |
| Reopen | Outage + repair | 22.21 | 29.88 / 53.05 | 4.57 / 17.03 | 2.15 / 6.37 |
| Reopen | After | 52.95 | 28.13 / 44.53 | 3.99 / 8.28 | 1.78 / 3.30 |

Initial deploy/feed/index/logical verification took **470.32 s / 212.62 docs/s**.
Twelve native cgroups used **915.86 core-seconds** (**817.20 user / 98.67 system**)
around that load command, excluding image builds and earlier server bootstrap.
Raw `load-resource-*.json` files preserve per-host elapsed times, charged memory
and cgroup-lifetime peaks. Server ceilings are **15 CPUs / 19.5 GiB**, with tools
at **2 CPUs / 3 GiB**, on the same **11-vCPU / 24.42-GiB** Docker VM: CPU ceilings
are oversubscribed, not dedicated machine resources.

One post-recovery warm run timed **1,000 requests per row** after **16 warmups**;
all **8,000 requests succeeded**. Dense recall@10 was **0.9963 at one client /
0.9962 at eight clients**. Different budgets, replication, per-shard ANN effort
and recall prevent interpreting these numbers as normalized scaling or a universal
winner. Three-trial comparative, saturation/soak and cold-cache claims remain unrun.

| Search | Clients | Queries/s | p50 ms | p99 ms |
|---|---:|---:|---:|---:|
| lexical | 1 | 458.94 | 2.12 | 3.65 |
| lexical | 8 | 1,213.03 | 6.33 | 12.80 |
| dense | 1 | 217.16 | 4.35 | 5.91 |
| dense | 8 | 660.00 | 11.20 | 19.74 |
| hybrid | 1 | 200.57 | 4.93 | 6.83 |
| hybrid | 8 | 631.67 | 12.20 | 22.57 |
| rerank | 1 | 192.78 | 5.10 | 7.41 |
| rerank | 8 | 607.50 | 12.74 | 23.78 |

The native cgroups used **85.20 CPU core-seconds** (**79.41 user / 5.79 system**)
across **28.33–28.35 seconds** per host around the entire benchmark command,
including warmups/quality/housekeeping and counter inspection. Workload CPU is
excluded. Afterward, summed charged memory was **13,273,427,968 bytes**. Summed
member lifetime peaks were **14,389,010,432 bytes**; this is not a simultaneous
whole-stack peak or benchmark-only peak, and includes earlier recovery/testing.
Neither measure is process RSS. Native container and daemon identities stayed
unchanged during measurement; raw counters and measurement source are saved.

Cleanup hash-verified **887 files**, left **zero owned containers, volumes,
networks or built tags**, and preserved all ten recorded foreign-volume identities.
Evidence: `results/production/sharded/previous-runs/8d57a56a3eb542d7b9d9b781d6382747/`;
benchmark `benchmarks/88fad3c8d7e7495b8695a27693d271f8/` inside that archive.
Teardown proof: `results/teardown-check-8d57a56a3eb542d7b9d9b781d6382747.json`.

Partitions/quorum loss, membership/resource scaling, upgrades, pressure/EIO,
independent backup restore and whole-sharded-stack SIGKILL remain unrun. These
Compose replicas share one Docker VM and do not establish independent-host
durability. Native per-node API readback is not independent on-disk decoding.

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
