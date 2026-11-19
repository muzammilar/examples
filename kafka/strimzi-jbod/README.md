# Kafka on Strimzi — live JBOD recovery

Three dedicated KRaft controllers and three brokers in an example-owned kind
cluster. A CMake C++ producer writes protobuf orders while an independent Rust
consumer checks bodies, partition ordering and offsets. External fsynced journals
retain attempts, delivery outcomes and reads through each failure and repair.

## Quick start

1. `make kind-up` — build/check the workload images and create the isolated kind cluster.
2. `make up` — install pinned Strimzi/Kafka and load the workload images.
3. `make test` — verify at least 1,000,000 acknowledged orders and all retained histories.
4. `make down` — replay retained ACK histories, then remove Kafka and the operator.
5. `make kind-down` — remove kind, its private kubeconfig and example-built images; archive witnesses/results.

Prerequisites: Docker, Make, kind, kubectl, Helm and Python 3.12+ for controllers.
C++, protoc and Rust compile inside Docker. Every kubectl/Helm call names
`kind-kafka-strimzi-jbod`; Make exports `results/kubeconfig` before kind creation.
The default user context is untouched. Workload Pods run on the control plane;
one broker/controller pair runs on each of three workers. All share one host.

## Independent experiments

| Command | Operation |
|---|---|
| `make test COUNT=2000000` | Larger correctness run; default minimum is 1,000,000 ACKs |
| `make chaos-broker` | Discover a partition leader, SIGKILL its actual CRI container process, prove exit and native restart/ISR recovery |
| `make chaos-controller` | Discover the active KRaft leader, crash its process, verify replacement and restored quorum/replicas |
| `make chaos-disk` | Add a second log directory, move six replicas to one broker's volume 1, inject directory write denial, hold 30 seconds after native offline/ISR evidence, restore permissions and restart its container |
| `make test-production` | Run correctness, broker, controller and disk cases serially |
| `make benchmark` | Three fresh workload-process trials, 2,000,000 minimum ACKs/trial, offered live rate 50,000 records/s |
| `make verify-history` | Read back every retained history without generating new data |
| `make diagnose-history CASE_ID=<id>` | Inspect a failed history; diagnostic results are never strict passes |
| `make freeze-failed-history CASE_ID=<id>` | Record the unchanged failed physical state from two full scans; requires zero lost/corrupt/duplicated ACKs and terminated writers |
| `make status` / `make logs` / `make cli` | Inspect the owned lab |

`COUNT`, `LIVE_RATE` (default 1,000/s), `BURST_RATE` (default 0: unlimited), and
`DURATION_SECONDS` (default 1,800 s), and `DELIVERY_TIMEOUT_MS` (default 300,000 ms)
are Make variables. The producer bursts through
half the minimum dataset, then continues at the offered live rate during faults
and native repair. It never pauses for injection. After recovery the controller
requests completion; the producer continues until the minimum ACK count is met.
Individual cases have a four-times-minimum safety bound and bounded deadlines.
Benchmark knobs: `BENCHMARK_COUNT=2000000`, `BENCHMARK_RATE=50000`.

Each case creates a unique six-partition topic; it never resets earlier data.
RF=3, minimum ISR=2, `acks=all`, producer idempotence, unclean election disabled.
The producer journals intents before admission and records native delivery ACKs
with partition, full protobuf SHA-256 and a known or explicitly unknown offset.
A successful idempotent duplicate-sequence retry can have no usable offset;
require native persisted status, then bind its body to exactly one replayed
partition/offset. Known ACK offsets must match exactly. Delivery failures stay ambiguous;
a later visible body does not turn one into an ACK. Queue-admission retries and
native retry statistics are retained separately from application mutation retries.
The reader fsyncs its observations before committing explicit next offsets.

Both actors remain live through repair. Native checks require all three ISR
members, zero log-directory replica lag and the original topic/cluster identities.
A fresh Rust verifier replays every retained topic through fixed native end offsets,
checking every acknowledged body and physical position. Final committed consumer
offsets must equal native end offsets. Host reconciliation checks the complete live
reader journal too. Duplicate deliveries at the same offset differ from duplicate
logical IDs at different offsets; the latter fail.

The disk case then makes the repaired broker preferred leader for every retained
partition and replays their complete bodies through it. This proves service from
the repaired member; it does not independently decode every follower's disk bytes.

## Storage boundary and disk walkthrough

The lab uses ephemeral EmptyDir storage. Adding a JBOD volume recreates broker
Pods and loses their previous local volumes; surviving replicas rebuild them.
A container-process restart retains the Pod's volumes. These are different faults.
Strimzi recommends block storage for production; this lab does not establish
independent-host power-loss durability or production disk performance.

Volume 0 holds the KRaft metadata directory and remains untouched. Write denial
on volume 1 is an injected permission error, not physical device failure or EIO.
Small segments and a 5-second roll make Kafka create new files: chmod alone does
not revoke writes through already-open file descriptors. The test requires a fresh
native offline-directory error and ISR loss, not a grep of old logs. Permissions
are restored on failure without resetting data.

The separate Cruise Control walkthrough remains available after `make up`:

1. `make disk-add` — add volume 1 on all brokers.
2. `make disk-rebalance-plan` — inspect a `ProposalReady` intra-broker proposal.
3. `make disk-approve` — approve and wait for completion.
4. `make disk-remove-plan` — inspect removal of volume 1.
5. `make disk-approve` — move replicas off it.
6. `make disk-remove-finish` — remove the now-empty directory from the pool.

Do not remove a disk while it owns replicas. Run the automated disk-fault case
from either its initial one-volume topology or its own retained two-volume state.
`disk-remove-finish` requires the actual `remove-disks` plan and native proof that
all added directories contain zero partitions; an unrelated Ready plan is refused.

## Measurement boundaries

Raw journals, phase markers, native membership/log-directory snapshots, manifests,
controller source snapshots and results live under ignored `results/`. Failed
histories remain failed, even when a later replay proves their bodies survived.
Teardown requires a fresh replay before removing storage and preserves evidence.
A deliberately frozen failed history stays labelled FAILED: later tests require
its full per-partition physical checksums, offsets and counts to remain identical.
This checks retention of the known failed state; it does not turn that original
experiment into a passing idempotence test or permit ACK loss/corruption.

Rates include fsynced witness and validation costs. Write latency covers admission
through delivery callback, including native retries. Message-poll latency may
serve prefetched data; producer-created-to-reader-observed latency is separate.
Periodic ListOffsets calls measure a read RPC, not a body read. Success and error
latencies/counts are separate, with 1-microsecond histogram buckets. Fault workloads
are rate-limited observations, not capacity benchmarks.

The benchmark uses fresh applications against a retained warm Kafka cluster, not
three fresh engines or cold caches. CRI samples target five-second cadence and
retain cumulative all-thread cgroup CPU plus RSS/charged/working-set memory.
Sample maxima are not guaranteed peaks; work after the last sample can be missed.
Native CLI observers, Kubernetes and witness overhead must be disclosed with results.

## Verified production cases

2026-11-19: Apple M4 Pro (14 host cores, 48 GiB RAM), Docker Linux aarch64
(11 CPUs, 24.42 GiB RAM). One serial `make test-production COUNT=1000000`
aggregate passed with `DELIVERY_TIMEOUT_MS=300000`: **4,004,096 ACKs**, zero
ambiguous deliveries, missing/corrupt ACK bodies or duplicated acknowledged orders.
Each case wrote 68,960,570 B of protobuf payload (65.77 MiB). Every retained
history was checked; the known failed history below remained explicitly failed.

| Case | ACKs | Unknown ACK offsets | Write callback p50 / p99 ms | Message observation p50 / p99 ms | Watermark / message-poll errors |
|---|---:|---:|---:|---:|---:|
| Correctness | 1,001,024 | 0 | 11.290 / 889.477 | 3745.356 / 7851.063 | 0 / 0 |
| Broker crash | 1,001,024 | 2 | 11.471 / 796.043 | 3270.616 / 6799.318 | 6 / 2 |
| Controller crash | 1,001,024 | 0 | 11.392 / 854.365 | 3567.183 / 7417.096 | 0 / 0 |
| Disk write denial + restart | 1,001,024 | 6 | 11.519 / 11950.524 | 2864.029 / 12411.340 | 2 / 0 |

These percentiles cover each complete workload, including bursts and client
backlog; message observation is creation-to-decoding, not a fetch-RPC benchmark.
The configured live upper bound was 1,000 records/s between bursts. Actual rates,
counts and latency histograms for each phase/partition remain in `measurements.json`.
Native retries are included; application mutation retries were zero.

| Fault | Container restart → Ready | Injection → observed native repair |
|---|---:|---:|
| Broker crash | 23.5 s | 30.4 s |
| Controller crash | 23.4 s | 29.7 s |
| Disk write denial | 42.5 s after the recovery SIGKILL | 98.7 s from initial write denial |

Disk-offline detection took 12.0 s; the test deliberately held the fault for
30 s after confirmation. Repair times include polling and controller work.
The repaired broker became preferred leader for every retained partition at
121.4 s after injection; fresh full-body replays then read through that member.
Kubernetes identity polls run at 1 s; a native health observation includes three
serial JVM tools (about 6–7 s). Election and replica catch-up were not timed
separately. One current host/VM clock probe bounded offset to -6.0…45.8 ms;
sub-millisecond completion-after-marker values are not outage measurements.

### Failed short-deadline experiment: preserved, not passed

With the same Kafka/SDK versions and a 10,000 ms delivery deadline, the disk test
failed logical uniqueness during its offline hold. After native recovery:

| Observation | Count |
|---|---:|
| Attempts / recorded ACKs | 587,040 / 574,474 |
| Ambiguous delivery outcomes | 12,566 |
| Unique visible ambiguous orders | 8,365 |
| Extra physical copies among ambiguous orders | 10,427 |
| Lost / corrupt / duplicated acknowledged orders | 0 / 0 / 0 |
| Total physical records | 593,266 |

A native Java consumer independently confirmed the same 69-byte order at offsets
94,403 and 94,734 in partition 3 (SHA-256
`b042571f3024bb1b81cea90b3d8386be4790e4a9aab2a54933ce803ec549b63c`).
Its producer outcome was `Local: Message timed out`, not an ACK. Native statistics
record producer-epoch churn. All 593,266 binary records were exported and checked
against durable intents and the Rust reader's per-partition fingerprints.

The original test remains **FAILED**. Two independent complete scans froze its
observed state; later operations require those exact bytes/offsets/counts to remain
unchanged, alongside complete ACK reconciliation. No records were removed or
replayed to repair the result. The five-minute variant above passed once on the retained two-directory topology;
it did not repeat the first run's preceding pool rollout. This is an observed
configuration/state difference, not an isolated upstream root cause or a universal
idempotence guarantee. The example defaults to the larger delivery
budget recommended by librdkafka. Applications still need stable IDs and explicit
reconciliation of timed-out sends.

Three earlier harness failures are also retained: final consumer commit returned
`NoOffset`; resource reporting assumed stopped CRI entries had CPU counters; an
old ACK check rejected legitimate unknown offsets after idempotent retries. Each
was corrected and requalified; their original failed reports remain unchanged.

## Benchmark

`make benchmark` passed three fresh workload-process trials against the same
retained warm Kafka cluster. Each requested at least 2,000,000 ACKs, with a
configured live upper bound of 50,000 records/s between bursts. This measures the
C++/Kafka/Rust path with protobuf validation and durable witnesses; it is neither
a Kafka saturation test nor a comparison with another product. No cold-cache or
alternative batching sweep was run. Native batching uses `linger.ms=5` and LZ4.

| Trial | ACKs / attempts | Payload B | Admission → final ACK s | ACKs/s | Payload MiB/s | Callback p50 / p99 ms | Message observation p50 / p99 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 2,001,024 / 2,001,024 | 138,960,570 | 60.149 | 33,267.58 | 2.203 | 10.832 / 1248.716 | 5437.332 / 12518.893 |
| 2 | 2,002,048 / 2,002,048 | 139,032,250 | 60.006 | 33,364.39 | 2.210 | 10.877 / 705.332 | 5890.495 / 12647.985 |
| 3 | 2,001,024 / 2,001,024 | 138,960,570 | 59.997 | 33,352.06 | 2.209 | 10.866 / 720.255 | 5860.597 / 12310.233 |

Median of the three trial summaries: **33,352.06 ACKs/s**,
**2.209 MiB/s**, callback p99 **720.255 ms**.
These are not pooled percentiles. Each trial has at least 2,001,024 successful
write/message samples. All 6,004,096 ACKs were reconciled, with zero ambiguous
deliveries, lost/corrupt bodies or duplicate logical records in those trials.
Application mutation retries were zero; queue-admission retries were 52, 5 and 0.
Native retries remain separately recorded in native statistics.

The throughput clock runs from order 0's first admission attempt to the final
delivery callback. It includes subsequent serialization, SDK queuing and witness
work, but excludes initial cluster creation, the first batch's preparation,
final witness close and historical replay. Message observation includes producer
queuing and reader backlog. It is not a network-fetch latency measurement.

| Trial | Observed producer CPU core-s | Producer / reader sampled max RSS MiB | Verifier sampled max RSS MiB | CRI-pod CPU core-s, including historical verification |
|---|---:|---:|---:|---:|
| 1 | 23.653 | 81.83 / 388.80 | 754.02 | 206.797 |
| 2 | 22.499 | 81.36 / 410.39 | 849.09 | 218.547 |
| 3 | 23.434 | 77.60 / 395.82 | 843.32 | 216.246 |

Five-second CRI sampling produced 204, 224 and 232 node samples. CPU uses cumulative
cgroup core-nanoseconds across all threads; short-lived work/final unsampled tails
can be missed. RSS values are sampled maxima, not guaranteed peaks. Charged
memory/working-set samples are retained separately. Whole-case CPU includes
Kubernetes containers, JVM observers and increasing historical readback, so it is
not a fixed-size producer-only cost comparison. Host controllers, node runtimes/kubelets, Docker VM
kernel overhead and cluster installation costs are excluded.

Budget: three broker + three controller CPU limits of 1 each, 1,536 MiB each;
Cruise Control 1 CPU / 1,536 MiB; cluster operator 1 CPU / 384 MiB. Topic/user
operator containers had no individual limits and shared the VM budget. Producer
1 CPU / 512 MiB, reader 1 CPU / 1 GiB, and one serial verifier at 2 CPUs /
2 GiB for two-million-message plans (1 GiB for million-message histories).
All roles share the 11-CPU / 24.42-GiB Docker VM and its virtual disk; there are
no dedicated IOPS/network caps. This is a client/witness workload result on one
host, not evidence of production disk capacity or independent-host durability.

The input is four non-null protobuf fields: order ID, customer ID, signed integer
amount and creation time. There are 10 customer IDs and one unique Kafka key per
order, uniform customer rotation, sequential order numbers scoped by a captured
random run UUID, and live timestamps. Full inputs are identified by per-order
payload hashes in the durable intent journal; no fixed random corpus is implied.
Producer witness artifact SHA-256 checksums:

- Trial 1, `49cb69b1a51a4129848479c996d8e09a`: `1d6138be136fab3401f5727a13a12a9253163f6e4bc035f9ca2acbcc1db3dda7`.
- Trial 2, `d7833c50cbe44a999b8e96671657cc0f`: `060311399cc69ad61c155dd07468ca6bfe7be1762904e5c5cd4d975a44f31900`.
- Trial 3, `8e76e6f7c2a344b9afb5b96df1e9f079`: `bf15eaecd9f6441a8e99f6439d46e0100a5c9a30589b88cf9430bf5bfd153028`.

## Historical runs

On 2026-11-14, `make chaos-broker COUNT=1000000` deleted broker pod `kafka-brokers-3` after the live producer had enqueued about 410,000 records. The producer completed and the Rust consumer validated 1,000,000 unique orders with an amount sum of 3,499,500,000 cents. `make chaos-disk COUNT=1000000` injected a write-permission failure on broker 3's volume 1 while producing; Kafka reported a failed log directory, the producer completed, the broker recovered after the directory was restored and its pod restarted, and the Rust consumer validated all 1,000,000 unique orders with the same amount sum. These counts verify correctness and recovery, not throughput. The disk fault is a filesystem permission error, not a physical-device failure. A separate earlier 100-order disk walkthrough added volume 1 to brokers 3–5, completed 9 intra-broker movements for the rebalance proposal and 189 movements for the remove-disks proposal, then returned all 3 brokers to one volume; Cruise Control reported 0 MB to move in both plans. Those disk-movement figures are not measurements from the million-order runs.


These historical tests used the earlier one-shot consumer and do not establish
continuous read availability, preserved cross-case witnesses or the new barriers.

## Setup and remaining limits

| Component | Version / budget |
|---|---|
| kind | `kindest/node:v1.34.0`, one control plane + three workers |
| Strimzi / Kafka | `1.3.1` / `4.3.2` |
| Kafka processes | 3 controllers + 3 brokers, each CPU limit 1, memory 1,536 MiB, JVM heap 512 MiB |
| Cruise Control | CPU limit 1, memory 1,536 MiB; operator/entity services add costs |
| Producer | C++20/CMake/protobuf; source-checksummed librdkafka `2.12.1`; CPU 1, memory 512 MiB |
| Reader / verifier | Rust `1.91`, rdkafka `0.39.0`, native `2.12.1`, prost `0.14.1`; CPU 1 / 2; reader 1 GiB, verifier 1 GiB for million-message plans or 2 GiB above that |

The synthetic orders have unique run/order IDs, 10 customer IDs (Kafka keys are
unique order IDs), integer amounts
1,000–5,999 cents, and creation timestamps. No corpus or string-heavy workload is
implied. Report actual serialized input bytes per case, not Kafka persisted size.
No TLS/SASL is configured; use only this isolated development cluster.

Scaling horizontally/vertically, operator outage/drift/invalid CRs, rolling
maintenance, worker restart, PDB/drain, partitions/quorum loss, upgrades, bounded
pressure, persistent-PVC replacement, whole-stack cold reopen and independent
backup restore remain unrun in this backfill. The core aggregate does not qualify the full operator matrix.
Redpanda comparison, batching/linger sweeps and saturation/soak measurements also
remain unrun. Full teardown archived and hash-verified 1,535 files under
`results/archives/45ac3389ca4f4b4bb9822a096685bf3d/`. The private cluster/kubeconfig
were removed, and 23 unrelated Docker volumes were preserved.

## Layout

- `producer/`, `consumer/`: application source, dependency locks/builds and Dockerfiles.
- `cluster.py`: private cluster ownership and workload Job admission.
- `control.py`, `report.py`, `resources.py`: serial operations, reconciliation and measurements.
- `manifests/`: pinned KafkaNodePools/Kafka and Cruise Control plans; the controller creates each case's topic.
- `scripts/`: native log-directory reassignment helper for manual inspection.

## Sources

- [Strimzi 1.3.1 deployment/storage guidance](https://strimzi.io/docs/operators/1.3.1/deploying).
- [Pinned Kafka reconciliation source](https://github.com/strimzi/strimzi-kafka-operator/blob/1.3.1/cluster-operator/src/main/java/io/strimzi/operator/cluster/operator/assembly/KafkaReconciler.java).
- [Kafka 4.3 native operations](https://kafka.apache.org/43/operations/basic-kafka-operations/), [4.3.2 ReplicaManager log-directory failure handling](https://github.com/apache/kafka/blob/4.3.2/core/src/main/scala/kafka/server/ReplicaManager.scala).
- [librdkafka 2.12.1 guarantees/source](https://github.com/confluentinc/librdkafka/blob/v2.12.1/INTRODUCTION.md).
- [Rust consumer 0.39.0 commit/offset contracts](https://github.com/fede1024/rust-rdkafka/blob/v0.39.0/src/consumer/mod.rs).

- [Pinned idempotent duplicate-sequence handling: successful delivery with invalid offset/timestamp](https://github.com/confluentinc/librdkafka/blob/v2.12.1/src/rdkafka_request.c).
