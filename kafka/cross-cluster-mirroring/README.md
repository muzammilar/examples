# Kafka — cross-cluster mirroring with MirrorMaker 2

Two independent Strimzi-managed KRaft Kafka clusters run in one local kind cluster. `east` starts as the active cluster and accumulates the workload before Apache Kafka MirrorMaker 2 (MM2) is deployed. MM2 then mirrors to the standby `west` cluster. The test switches the consumer group to `west` at its checkpointed offset, then switches the producer to `west` and validates the new batch there.

## Quick start

1. `make kind-up` — create the kind cluster.
2. `make up` — install Strimzi and start both Kafka clusters; MirrorMaker 2 is not started yet.
3. `make test` — send 2,000,000 protobuf orders to `east`, then start MM2, validate all 2,000,000 mirrored orders on `west`, wait for the `dr-*` consumer checkpoint, switch that consumer group to `west`, and send and validate 2,000,000 new orders on `west`.
4. `make down` — remove both Kafka clusters, MirrorMaker 2, the operator, and the namespace.
5. `make kind-down` — delete kind and the example's local client images.

Prerequisites: Docker, `make`, `kind`, `kubectl`, Helm, Python 3.9+, `envsubst`, and `grep`. Producer and consumer images build with Docker; host C++, protobuf, and Rust toolchains are not required.

Every run uses a unique topic such as `orders-20261114070120` and run ID, so it does not need to erase existing topic data. Override `COUNT`, `TOPIC`, or `RUN_ID` with Make variables. `TOPIC` must match `^orders-[0-9]+$`, the configured MM2 filter; setup rejects excluded names before creating a topic or producing records. The C++ producer accepts counts up to 2,000,000. The default test validates 4,000,000 unique orders across the initial source batch and producer cutover batch, and also validates the mirrored copy of the initial batch.

Make exports the example-owned `results/kubeconfig` before kind creation and
removes it at `kind-down`; kubectl and Helm always specify the full kind context.
The default user context is not changed. Docker Desktop may report macOS bind
sources under `/host_mnt`; ownership accepts that exact translated path only on
macOS, still requiring a writable bind to this example's witness directory. Fresh
recreation exposed this path-check failure; after correction, a host-written
ownership receipt was read back through the actual control-plane mount.

## Live recovery tests

Run these after `make up`; the source writer and **independent east and west Rust
readers** remain live before injection, throughout native recovery and afterward.
`COUNT` is a minimum acknowledged count, default **2,000,000**; the supported range
is 1,000,000–2,000,000. A final in-flight batch can exceed it by 1,024 records.
`LIVE_RATE=1000` is the post-warmup offered upper bound, not measured capacity.
The producer first bursts 100,000 orders, continues at that bound without pausing,
then finishes the minimum only after recovery and renewed reader progress.

| Target | Operation |
|---|---|
| `make verify-retained` | After cutover and repeated setup, use the original `TOPIC`, `RUN_ID` and `COUNT` to validate the source batch on east and both known batches on west through new groups. |
| `make test-live-mirror` | Establish the witnessed two-cluster correctness baseline. |
| `make chaos-mm2-worker` | Discover a running source-task owner and SIGKILL its real container process. Prove old-process exit, replacement identity, restored tasks and stable unrelated containers. |
| `make chaos-target-broker` | Discover the actual west partition-zero leader, SIGKILL it, retain its Pod/data volume, then verify native catch-up. Reorder preferred replicas and elect the recovered broker leader for all six new-topic partitions before complete replay. |
| `make test-production` | Serial baseline → worker crash → retained-volume broker crash → empty-disk replacement; stop on any failure. Component targets were executed separately; this four-case aggregate invocation has not been run. |
| `make chaos-broker-disk-loss` | Fence the actual west leader, replace its EmptyDir, rebuild RF3 and promote the rebuilt copy under live load; replay every preserved ACK history. |

Cutover and live-fault/teardown controllers reject concurrent runs using local
file locks. Wait for a stopped controller and its child processes to exit before
retrying; preserve the interrupted attempt's evidence.

Live qualification currently requires identity replication policy, two MM2 workers
and three source tasks. The separate cutover/configuration experiments below keep
their existing knobs. The recovery tests validate all six partitions, RF=3/ISR=3,
every native log directory's replica lag, distinct metadata cluster IDs, three
caught-up voters per cluster and running Connect tasks. A Ready Pod alone cannot
pass. Source ACK partition/offset/body witnesses are checked against replay; west
must preserve the partition and exact bytes while using its own physical offsets.
Duplicate logical records at different offsets fail. Application mutation retries
are disabled; SDK admission retries, ambiguous responses and read errors remain
separate recorded outcomes.

The C++ producer fsyncs intents before admission and records ACK/rejected/ambiguous
outcomes. Readers fsync observed IDs, hashes, timestamps, positions and errors
before committing explicit offsets. Their journals and phase markers live under
ignored **host-backed** `results/production/`, mounted only into the owned kind
control plane; broker/worker process failures cannot delete them. They are not an
independent backup against host/disk loss. Every successful case replays earlier
histories on both clusters instead of resetting their data. Cleanup refuses an
unqualified history. Preserve the witness and diagnose failed cases before retrying.
After native repair, `python3 scripts/replication_control.py reconcile-history` performs
read-only full-history verification and seals a separate recovery receipt; it does
not turn failed experiments into passes. `make kind-down` moves the retired lab's
histories into `results/production/archives/<owner>/`, with a SHA-256 manifest,
so a recreated cluster cannot accidentally claim to retain its predecessor's data.

`kind-config.json` is the tracked topology template. `make kind-up` substitutes the
owned host witness path into ignored `results/kind-replication.json`; it records
and checks exact Docker node identities and the mount. It refuses to adopt an
existing cluster without that receipt. Retire a pre-witness cluster through its
previous lifecycle before upgrading; do not delete receipts to bypass ownership.

### Executed recovery results — 2026-11-19

All three completed cases used **2,001,024 distinct orders / 138,960,570 serialized
protobuf bytes (132.523 MiB)** each: **6,003,072 acknowledged orders** cumulatively.
Both clusters' complete readbacks found **zero missing/corrupt orders**, zero
ambiguous producer outcomes and zero duplicate live deliveries. All earlier ACK
histories were reconciled after each completed operation.

| Case | Source ACKs | Source ACK callback p50 / p99 (ms) | Process-restart barrier (s) | Native recovery barrier (s) |
|---|---:|---:|---:|---:|
| Baseline `b007c649907a4b2ab4b606aec58d5157` | 2,001,024 | 13.541 / 905.372 | — | — |
| MM2 worker `ca829246c516404081f37645b9bebe8e` | 2,001,024 | 12.500 / 753.505 | 63.396 | 76.415 |
| West broker `8d56f48ba3484710b85700cd5b439d60` | 2,001,024 | 15.880 / 898.947 | 24.264 | 37.303 |

Times begin at the controller's fault marker. Broker recovery was followed by
preferred-leader promotion; the recovered copy served all six partitions at
**72.569 s**. These are observed barriers, including CLI startup/polling, not exact
election instants. Progress polling is approximately 1 second; a full native sweep
also runs six JVM inspection commands. Election and catch-up were not independently
timed. Buffered completions can arrive after injection; they do not prove an endpoint
remained available.

| Case | East creation→observation p50 / p99 (ms) | West creation→observation p50 / p99 (ms) | West poll / watermark errors |
|---|---:|---:|---:|
| Baseline | 14912.411 / 31789.825 | 17532.912 / 30370.457 | 0 / 0 |
| MM2 worker | 9271.098 / 27063.035 | 11208.931 / 29074.734 | 0 / 0 |
| West broker | 18929.488 / 38064.607 | 20256.985 / 32668.115 | 2 / 5 |

Each message percentile uses 2,001,024 successful observations. Creation→observation
includes intent fsync, producer/SDK queues, replication and observer backlog; it is
not isolated MM2/server execution latency. SDK message polls can return prefetched
records and are not network query latency. The broker case's two failed polls had
p50/p99 **61.664/62.191 ms**; five failed watermark requests had **1000.880/1001.073 ms**.
Those tiny error samples describe the observed requests, not established tail
percentiles. The largest west completion gap was **9.392 s**; per-partition gaps and
before/during/recovery histograms remain in each case's raw report. Successful
watermark-request p50/p99 were **2.307/476.024 ms** in that case (179 samples).

The mixed warmup/steady/final-burst source ACK windows measured **18,216.11**,
**13,359.08** and **12,562.60 ACK/s**, respectively, between each first and last ACK.
These include deliberate rate limits and application/witness costs; they are
recovery-workload diagnostics, not Kafka capacity, cold-cache measurements or a
three-trial comparative benchmark. No competitor ranking is established.

Dataset: four non-null protobuf fields; unique run-scoped order IDs (also Kafka
keys), ten round-robin customers, integer amounts from 1,000–5,999 cents, and actual
creation timestamps. The run UUID and every intended body's SHA-256/byte length
are retained. No external corpus or probabilistic generator is used. Physical
compressed Kafka size, peak CPU/RSS and whole-stack peak memory were not measured
in these runs; do not infer them from serialized payload bytes or configured caps.

Platform: Apple M4 Pro (14 cores, 48 GiB host RAM), Docker 29.5.3 aarch64 VM with
11 CPUs and 26,216,296,448 bytes RAM. Shared virtual disk/kind bridge, no IOPS or WAN
limits, one control plane/three workers. Each controller is capped at 1 CPU/1 GiB,
each broker at 1 CPU/1536 MiB, each MM2 worker at 1 CPU/1 GiB; Kafka heaps stay
256 MiB. Producer: 1 CPU/512 MiB; each live reader: 1 CPU/1 GiB; replay verifier:
2 CPUs/2 GiB, serial across sides/histories. These are shared-host limits, not
independent machines or dedicated reserved cores; operator/entity services and
Kubernetes overhead are additional costs.

### Empty-disk replacement — 2026-11-20

Fresh case `fe14fbcc93ef498f957e480abc04857d` passed with **2,001,024 new
ACKs / 138,960,570 protobuf bytes**, zero rejected/ambiguous writes, zero ACK
loss/corruption and zero duplicate live deliveries on either cluster. Full replay
also checked the four earlier data histories: **10,005,120 ACKs** per cluster.
The interrupted experiment remains failed even though its data reconciled.

The controller fenced the actual west leader (broker 5), briefly stopped only its
owned worker's kubelet to prevent an old-volume restart race, normally deleted the
Pod, and resumed kubelet in cleanup. It positively verified the old UID's storage
was removed and the replacement had a different UID/data path. Native checks
required all replicas caught up before promoting the rebuilt broker to leader of
all six partitions. Unrelated Kafka/MM2 containers retained their identities.
This is permanent EmptyDir loss on one shared VM, not a physical-device failure.

| Observation | Measured result |
|---|---:|
| Fault → positive process fencing | 1.179 s |
| Fault → replacement Ready | 29.442 s |
| Fault → native recovery barrier | 42.464 s |
| Fault → rebuilt copy serving all six partitions | 72.070 s |
| Source ACK callback p50 / p99 | 11.711 / 994.007 ms |
| East creation→observation p50 / p99 | 11,572.942 / 26,189.878 ms |
| West creation→observation p50 / p99 | 13,650.099 / 26,805.402 ms |
| West failed polls / watermark requests | 6 / 5 |
| West longest observed completion gap | 9.425 s |
| Largest west per-partition completion gap | 58.681 s |

The mixed source ACK window was **13,723.43 ACK/s**; rate limiting, fsynced
witnesses and final burst apply as above. Successful message percentiles each have
2,001,024 samples. Failed poll p50/p99 were **1.347/49.546 ms** (6 samples);
failed watermark p50/p99 were **1001.066/1001.958 ms** (5 samples). These small
error samples do not establish tail latency. Completion gaps include client
buffering/scheduling and do not establish a continuous endpoint outage.

CRI sampling recorded **408 node samples over 505.476 s**, targeting 5-second
cadence, and **713.214 observed CPU core-seconds** across sampled containers,
including Kubernetes, workloads and historical replay verifiers. Largest sampled
individual-container RSS was **959,320,064 bytes** (MM2 worker 0); largest charged
memory was **1,610,326,016 bytes** (west broker 5). These are sample maxima, not
true peaks or simultaneous whole-stack memory. Stopped-container counters may be
unavailable and short-lived work/tails can be missed. Host controllers and Docker
VM overhead are excluded. This is experiment accounting, not a capacity comparison.

### Recreated lab and retained state — 2026-11-20

A new four-node kind lab was created after archiving the recovery cohort. Its
initial ownership check rejected Docker Desktop's translated bind path; the fix
above and an actual host-file readback qualified the mount before workload setup.

The serial locked cutover (`TOPIC=orders-20261120064000`,
`RUN_ID=lifecycle-20261120-v3`, `COUNT=2000000`) passed: **2,000,000 initial
orders**, their validated west copy, then **2,000,000 west-produced orders**.
There were **4,000,000 unique orders**, each batch totaling **6,999,000,000 cents**.
The checkpoint gate rejected two premature snapshots, then proved all six exact
translations and **zero application replay** on attempt 3 after **39.125 s**.
This polling/CLI barrier is not an isolated replication-latency benchmark.

A separate `make up` invocation retained all **15 managed Pod UIDs/container
identities**. `make verify-retained` then replayed **2,000,000 east records** and
**4,000,000 west records** through fresh groups, validating both batches' complete
expected bodies/keys, unique IDs and separate creation-timestamp bases. The
consumer's optional `SECOND_RUN_ID` declares the second batch explicitly; unknown
runs and invalid bodies still fail. West's combined amount was
**13,998,000,000 cents**. Final native inspection found RF3/ISR3, zero log-directory
replica lag and zero metadata-follower lag on both clusters.

The deterministic cutover verifier stops at its declared counts. The separate live
ACK-history tests above provide strict captured-EOF/physical-duplicate accounting.

Normal `make down` and `make kind-down` completed after these checks. All **280
files** in the recreated lab's archive passed an independent SHA-256 recheck, its
cluster/private kubeconfig were gone, and all **six unrelated containers** retained
their identities and running/stopped states. Archive:
`results/production/archives/8f012e60c7b44f6395261e887a28b933/`.

The first retained readback remains failed: its one-run verifier encountered the
second valid batch on west. The two-run implementation passed Docker's Rust
format/Clippy/release checks and **5 Rust tests** before the successful replay;
controller coverage is **15 Python tests** plus lint/format checks.

Two earlier cutover attempts remain invalidated: an excluded custom topic
(`orders-lifecycle-20261120`) produced no matching MM2 tasks, and its manual
cancellation left a Make parent running while a retry reused fixed-name Jobs.
Both controllers were stopped; neither result qualifies. Topic creation now
rejects excluded names before producing data, and the full cutover holds a lock
inherited by its Make child; competing tests/teardown fail closed. Separate source
readbacks verified those attempts' **4,000,000 source orders** after repeated
setup. This does not establish successful west replication/cutover for them.

### Failed and pending runs

- Setup `bc11fbef4c9a4a3f874fd140b494898b` remains **failed**: Connect status inspection
  through the Kubernetes service proxy timed out before any workload started.
  A separate read-only receipt proves no workload Jobs/start gates/journals and
  all six source/target low/high offsets zero. Later cases recheck that exact empty
  state without converting the failed experiment into a pass. Strimzi's REST
  NetworkPolicy permits MM2 peers and the operator; inspection now uses the actual
  MM2 container's loopback endpoint without broadening its policy.
- Empty-storage run `45ec30c4317447b19c9fc35798b08bc6` remains **failed/unqualified**:
  after native rebuild and preferred-leader promotion, a `kafka-log-dirs.sh`
  inspection exited **139**, followed by the reported Docker crash. Full final
  reconciliation did not complete in that experiment. A separate post-reboot
  reconciliation on 2026-11-20 verified all **8,004,096 ACKs** from its four data
  histories on both clusters, with zero loss/corruption. Its original failure
  remains unchanged. After reboot, one MirrorSource task reported an invalid
  internal-request signature despite Ready workers; the supported Connect restart
  API (`includeTasks=true&onlyFailed=true`) returned HTTP 202 and restored all
  three tasks. Authentication, source fencing and EOS were retained. The cause of
  the original exit 139 or the signature failure is not isolated.
- Witness-backed `make down` and `make kind-down` passed on 2026-11-20:
  every data history was replayed before deletion, owned image tags and the
  private kubeconfig were removed, and all **774 archived files** passed an
  independent SHA-256 recheck. All **six unrelated containers** remained present.
  Archive: `results/production/archives/9e4449dbe39b4adeab08af334462d376/`.
  The recreated lab and its corrected retained-state checks are described above.
- Further fault domains remain unrun: source-cluster loss/unplanned promotion,
  quorum/partitions, node outage, drain/PDB, operator outage/reconciliation, scaling,
  upgrades, pressure, independent restore, saturation/soak and fair comparisons.

### Archived evidence recheck — 2026-11-21

The [committed archive summary](reports/VERIFIED_RESULTS.json) records a 2026-11-21
recheck of **1,054 file hashes** across both completed archives; all matched.
**15 host controller/checkpoint tests** also passed. No live cluster faults were
repeated for this recheck, and original failed/interrupted verdicts stay failed.
At the user's request, bulky journals are being cleaned after publication while
reports and proof summaries remain. Deleted journals cannot be replayed from their
hashes. The unrun fault and benchmark cases above remain gaps.

### Local workload source

`replication-producer` is a repository-tracked symlink to
`../strimzi-jbod/producer`. `prepare_replication.py` materializes its tracked files
and this example's local `replication-observer/` into ignored `.build/replication/`
using hard links, with copies when unsupported. Docker builds that local context;
no sibling Makefile, generated file or previous lab state is used. Both languages
use **librdkafka 2.12.1**. The C++ application uses C++20/CMake/protobuf and the Rust
observer uses Cargo/prost/serde/flate2/SHA-256. Protobuf is compiled in Docker.
Docker builds executed C++ CTest (one test group) and seven Rust test groups plus
format/Clippy checks. `make check-controls` runs checkpoint, native-health,
source-versus-mirror position and scoped-cleanup negative controls without Docker.

The separate `make test` cutover uses idempotent `acks=all` writes. MM2 requires
exactly-once source support and `read_committed`; this is not a transaction across
both Kafka clusters. DR offsets are checkpointed every five seconds and applied
only while the destination group is inactive. The cutover barrier below requires
an already caught-up idle group; it does not establish live unplanned failover.

## Checkpoint verification

`make checkpoint-proof TOPIC=orders-... RUN_ID=...` is a barrier for an idle
source/target DR group **before** producer promotion. Kafka's supplied
`CheckpointFormatter` decodes MM2 records; no custom Java or Kafka wire decoder is
included. An example-owned Rust Pod then requires:

- six current upstream offsets exactly matching the native checkpoints;
- target commits exactly matching the translated downstream offsets;
- both groups natively `Empty`, before and after inspection;
- all six source and target partitions at RF=3/ISR=3 with a live leader;
- read-committed EOF on every target partition, with **zero application records**
  returned after seeking to its translated commit.

Raw offsets can include transaction control records. The check reads the actual
visible tail instead of accepting a numeric lag of one. A checkpoint produced
while mirroring is behind can cover the source commit yet translate to an earlier
target position; that snapshot must wait. The barrier does not reset offsets,
start a subscribed consumer, or mutate group commits. A replay-free barrier is a
stricter condition than MM2's general asynchronous failover contract, which permits
replay. After target production begins, this idle-source promotion barrier no
longer applies.

The Rust application also checks the Kafka key, unique order index, canonical order ID, deterministic
customer/amount and the producer's timestamp progression. Explicit partition
commits occur only after validation; automatic offset storage is disabled. The
initial timestamp is inferred from the input, not independently witnessed.
`make check-controls` checks malformed/incomplete checkpoint output. Docker runs
Rust formatting, Clippy with warnings denied, and four negative-control test
groups during compilation. Controller samples, native formatter output, Pod/image
identities, offsets and failures are fsynced under ignored `results/checkpoints/`.
Failed probe Pods remain available for diagnosis until example teardown.

### Measured checkpoint run — 2026-11-19

`make test COUNT=2000000 TOPIC=orders-2026111901 RUN_ID=checkpoint-20261119-01`
passed on the dedicated kind cluster. The source and mirrored validators each
checked **2,000,000 unique orders / 6,999,000,000 cents**. After consumer then
producer cutover, west validated **2,000,000 additional orders** with the same sum:
**4,000,000 unique orders / 13,998,000,000 cents** across the two producer phases.
These are correctness counts, not a capacity benchmark.

The checkpoint barrier took **48.203 seconds across four attempts**. The first
three Rust probes rejected visible application replay while MM2 caught up; their
raw failures remain preserved. The fourth verified all six partitions and zero
visible replay. This wait includes inspection/polling and Pod startup, not a
measured outage or election time. Checkpoint record sampling ends after a
2-second native consumer idle timeout; retry delay is 5 seconds and Pod observation
is every 2 seconds.

| Partition | Source commit | Translated / target commit | Observed read-committed EOF |
|---|---:|---:|---:|
| 0 | 332581 | 333253 | 333254 |
| 1 | 333899 | 334569 | 334570 |
| 2 | 333721 | 334392 | 334393 |
| 3 | 334144 | 334814 | 334815 |
| 4 | 333698 | 334372 | 334373 |
| 5 | 331957 | 332625 | 332626 |

The one-offset differences above yielded no application records; the Rust EOF
observations establish that result for this run. Evidence directory:
`results/checkpoints/20261119T143754Z-35542a264c854713bc1fa18ea749a6d3/`.
A final-source rerun with `TOPIC=orders-2026111902 RUN_ID=checkpoint-20261119-02`
also passed the complete 2,000,000 + 2,000,000-order workflow after adding the
canonical-ID rejection check. Its six-partition barrier passed on the first
attempt in **9.613 seconds**, under
`results/checkpoints/20261119T144459Z-39b07948ecf443fc8f936a8a899f0a85/`.
This retained-cluster rerun is not an independent fresh-cluster performance trial;
image/build timing and prior warm state differ.

Platform: Apple M4 Pro host, Docker 29.5.3 aarch64 VM with 11 CPUs and
26,216,296,448 bytes RAM; one control plane/three kind workers. The existing Kafka
pools request 250m CPU/512Mi per Pod with 256Mi heaps and do not impose per-Pod
limits; each MM2 worker is limited to 1 CPU/1Gi and the Rust probe to 1 CPU/256Mi.
This run does not establish a fixed whole-stack performance budget.

The live recovery qualification above adds independent readers and per-ID ACK
histories. It does not retroactively qualify the historical Pod deletions or the
interrupted empty-storage case.

## What it demonstrates

- Separate KRaft metadata quorums and broker pools for `east` and `west`, each with three controllers and three brokers.
- Strimzi's `KafkaMirrorMaker2` resource running Kafka's MM2 source and checkpoint connectors. MM2 uses the target cluster for its Kafka Connect config, offset, and status topics.
- MM2 uses `IdentityReplicationPolicy` so the promoted `west` cluster keeps the same topic name and can accept the producer after cutover. It mirrors one way only; do not configure both clusters as identity-policy sources, since that can create replication loops.
- MM2 copies the protobuf key, value, headers, and partition number; the Rust consumer decodes the original protobuf without a schema registry or transformation.
- `offset-syncs` map source record offsets to target record offsets. The checkpoint connector translates the `dr-*` consumer group's committed offsets for the identically named topic. The target consumer starts at that checkpoint before the target producer writes its new batch.
- Exactly-once source support is explicit: the Connect worker enables it, the MM2 source connector requires it, and its source consumer uses `read_committed`. This behavior applies to MM2's source-to-target Kafka writes; it is not a transaction spanning both clusters or application side effects.

## Architecture experiments

Run these against the same example with `make test`; no extra cluster or example directory is needed. The selected Make variables are rendered into `manifests/mirrormaker2.yaml`, and the cutover producer and consumer follow the selected target topic name.

| Question | Command | What to observe |
|---|---|---|
| Preserve topic names, or include source identity? | `make test MM2_REPLICATION_POLICY=identity` or `make test MM2_REPLICATION_POLICY=default` | `identity` mirrors `orders-*` as-is and is convenient for same-name promotion. `default` mirrors as `east.orders-*`; the test follows that prefix during target validation and cutover. Source-prefixed names help distinguish remote data and reduce accidental replication loops in multi-cluster topologies. |
| Where should MM2 keep offset-sync mappings? | `make test MM2_OFFSET_SYNC_LOCATION=source` or `make test MM2_OFFSET_SYNC_LOCATION=target` | Compare failover dependencies when the source is unavailable. This controls the location of the MM2 `offset-syncs` topic; it does not make checkpointing synchronous or remove the need to retain valid offset mappings. |
| How long should Connect wait for a departed worker before task reassignment? | `make test MM2_REBALANCE_MAX_DELAY_MS=300000` or `make test MM2_REBALANCE_MAX_DELAY_MS=30000` | The default is five minutes. A shorter grace can recover tasks sooner after a worker loss but can trigger reassignment during a temporary interruption. |
| How much replication parallelism should we allocate? | `make test MM2_REPLICAS=2 MM2_TASKS_MAX=3` or adjust either value | Watch MM2 worker/task logs and Kafka lag while balancing faster recovery and throughput against worker resources and rebalance cost. More tasks only help when the topic has enough partitions. |
| What is the tradeoff between fresh metadata and control-plane work? | Vary `MM2_TOPIC_REFRESH_INTERVAL_SECONDS`, `MM2_GROUP_REFRESH_INTERVAL_SECONDS`, `MM2_CHECKPOINT_INTERVAL_SECONDS`, `MM2_GROUP_OFFSET_SYNC_INTERVAL_SECONDS`, and `MM2_TOPIC_CONFIG_SYNC_INTERVAL_SECONDS` | The lab defaults are five seconds to make changes visible during a demo. Larger values reduce polling/sync activity and increase how long changes or offsets can take to propagate. |

Example: `make test COUNT=2000000 MM2_REPLICATION_POLICY=default MM2_REBALANCE_MAX_DELAY_MS=30000 MM2_REPLICAS=2 MM2_TASKS_MAX=3`. The defaults reproduce the current identity-name cutover path. MM2 documents `offset-syncs.topic.location` as `source` or `target`, and Kafka Connect documents a 300,000 ms default for `scheduled.rebalance.max.delay.ms`; the knobs above expose those decisions without changing the example's workflow. [MirrorMaker 2 configuration](https://kafka.apache.org/43/configuration/mirrormaker-configs/) · [Kafka Connect worker configuration](https://kafka.apache.org/43/configuration/kafka-connect-configs/)

### Experiment results

On 2026-11-14, `make test COUNT=2000000` passed with the defaults: the target validated 2,000,000 mirrored records, synced the six DR partition offsets, and completed consumer and producer cutover. With `MM2_REPLICATION_POLICY=default MM2_OFFSET_SYNC_LOCATION=source`, West received all 2,000,000 records under the `east.orders-*` topic name, but MM2 did not sync the DR group offsets; the run stopped before cutover. With both `MM2_REPLICATION_POLICY=default` and `MM2_OFFSET_SYNC_LOCATION=target`, the prefixed target topic remained empty and no DR offsets appeared. Those alternate combinations are recorded as failing experiments, not verified cutover configurations; use `identity` with source-side offset-sync storage for the passing path while investigating the alternatives.

## Other replication approaches

This example uses MM2 because it is part of Apache Kafka and Strimzi manages it directly. It runs on Kafka Connect workers, so there is an extra worker deployment and asynchronous offset translation to understand.

Confluent Cluster Linking is a broker-side alternative available in Confluent Platform and Confluent Cloud. It does not run Connect workers and preserves source topic offsets and compression in mirror topics. It is not included here because this example stays on Apache Kafka OSS. A custom consumer/producer relay is possible, but then the application owns replay, ordering, offset tracking, topic configuration, and loop prevention that MM2 already addresses.

## Historical run — 2026-11-14

`make test COUNT=2000000` passed on kind on 2026-11-14. The East consumer validated 2,000,000 unique protobuf orders with a sum of 6,999,000,000 cents. MM2 replicated the same 2,000,000 orders to West, where the validator confirmed the count and sum. MM2 synchronized the `dr-cutover-final-20261114` group offsets for all six partitions to the West topic tail (within one Kafka control offset). The consumer then resumed on West and validated 2,000,000 new orders from the West producer with a sum of 6,999,000,000 cents. The run checked 4,000,000 unique orders across both producer phases, or 13,998,000,000 cents in total, with the first batch also validated in its mirrored copy.

Both separate chaos tests passed on kind on 2026-11-14 with `COUNT=2000000`. In `make chaos-mm2-worker`, the first MM2 worker was deleted after the producer reached 10,000 records; the producer delivered all 2,000,000, the replacement worker returned, and both East and West validators confirmed 2,000,000 unique orders with a sum of 6,999,000,000 cents. Kafka Connect used its default five-minute scheduled rebalance grace before reassigning the checkpoint connector. In `make chaos-broker-disk-loss`, West broker 3 was deleted after production started; Strimzi recreated it, the producer delivered all 2,000,000, and the checkpoint and West validator passed with the same count and sum. These are correctness and recovery checks, not throughput benchmarks.

## Setup

| Component | Version / shape | Role |
|---|---|---|
| kind nodes | `kindest/node:v1.34.0`; one control plane and three workers | Local Kubernetes |
| Strimzi | `1.3.1` | Kafka and MirrorMaker 2 operators |
| Kafka clusters | Apache Kafka `4.3.2`; 3 controllers + 3 brokers per cluster | Independent east and west KRaft clusters |
| MirrorMaker 2 | Kafka `4.3.2`; 2 Connect workers | Replicates east to west |
| Producer | C++20, CMake, protobuf, librdkafka | Idempotent, compressed protobuf producer |
| Consumer | Rust, `rdkafka 0.39.0`, `prost 0.14.1` | Read-committed protobuf validator |

The two clusters run on the same kind nodes with ephemeral storage. They model separate Kafka metadata and broker clusters, but do not model separate physical hosts, regions, network links, persistent disks, or production security. Internal listeners use plaintext for this local demo.

## Layout

| Path | Purpose |
|---|---|
| `manifests/kafka-cluster.yaml` | Template for either independent Strimzi Kafka cluster |
| `manifests/mirrormaker2.yaml` | One-way MM2 flow, Connect state, checkpoint, and exactly-once settings |
| `producer/` | C++ producer and protobuf schema; `protoc` runs in the Docker build |
| `consumer/` | Rust read-committed consumer and protobuf generation |
| `jobs/` | Kubernetes Jobs for the cutover producer and consumer |
| `replication-producer`, `replication-observer/` | Witnessed C++ source and local Rust live/replay observer |
| `replication_control.py`, `replication_lab.py`, `replication_report.py` | Serial faults, owned lifecycle/Jobs and full-body/phase reports |
| `kind-config.json`, `prepare_replication.py` | Tracked kind template and ignored local build materialization |
| `checkpoint_probe.py`, `consumer/src/checkpoint.rs` | Native checkpoint sampling and Rust cutover barrier |

Use `make status`, `make logs`, and `make logs-mm2` to inspect the two clusters and replication workers. The kind context is always `kind-kafka-cross-cluster-mirroring`.

## Links

- [Strimzi 1.3.1 MirrorMaker 2 configuration](https://strimzi.io/docs/operators/1.3.1/deploying#assembly-mirrormaker2-str)
- [Apache Kafka 4.3 MirrorMaker configuration](https://kafka.apache.org/43/configuration/mirrormaker-configs/)
- [Apache Kafka 4.3.2 MirrorSourceTask](https://github.com/apache/kafka/blob/4.3.2/connect/mirror/src/main/java/org/apache/kafka/connect/mirror/MirrorSourceTask.java)
- [Apache Kafka 4.3.2 MirrorSourceConnector](https://github.com/apache/kafka/blob/4.3.2/connect/mirror/src/main/java/org/apache/kafka/connect/mirror/MirrorSourceConnector.java)
- [Apache Kafka 4.3.2 MirrorCheckpointTask](https://github.com/apache/kafka/blob/4.3.2/connect/mirror/src/main/java/org/apache/kafka/connect/mirror/MirrorCheckpointTask.java)
- [Confluent Cluster Linking](https://docs.confluent.io/platform/current/multi-dc-deployments/cluster-linking/index.html)

Checkpoint implementation references: [Kafka 4.3.2 native formatter](https://github.com/apache/kafka/blob/4.3.2/connect/mirror/src/main/java/org/apache/kafka/connect/mirror/formatters/CheckpointFormatter.java),
[native checkpoint schema](https://github.com/apache/kafka/blob/4.3.2/connect/mirror-client/src/main/java/org/apache/kafka/connect/mirror/Checkpoint.java),
[checkpoint freshness and group synchronization](https://github.com/apache/kafka/blob/4.3.2/connect/mirror/src/main/java/org/apache/kafka/connect/mirror/MirrorCheckpointTask.java),
and [console consumer error/idle-timeout handling](https://github.com/apache/kafka/blob/4.3.2/tools/src/main/java/org/apache/kafka/tools/consumer/ConsoleConsumer.java).

## Future work / TODO

Deferred checks for supported configurations; these are **not executed coverage**.
Keep recorded passes, failures and limitations above. Performance work requires
a separately selected depth; this list does not restart paused experiments.

- [ ] Lose the source cluster and exercise an explicitly fenced, unplanned promotion; reconcile source acknowledgements and translated consumer offsets.
- [ ] Exercise partitions/quorum loss, worker outage, drain/PDB and operator outage/reconciliation on the actual example-owned kind cluster.
- [ ] Change supported broker/MM2 membership and resources, then test supported rolling upgrades and bounded pressure.
- [ ] Extend disaster-recovery/restore scenarios with independent histories; preserve the already failed replication-policy configurations and interrupted run.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`reports/`](reports/): compact tracked result summaries.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
