# Kafka — cross-cluster mirroring with MirrorMaker 2

Two independent Strimzi-managed KRaft Kafka clusters run in one local kind cluster. `east` starts as the active cluster and accumulates the workload before Apache Kafka MirrorMaker 2 (MM2) is deployed. MM2 then mirrors to the standby `west` cluster. The test switches the consumer group to `west` at its checkpointed offset, then switches the producer to `west` and validates the new batch there.

## Quick start

1. `make kind-up` — create the kind cluster.
2. `make up` — install Strimzi and start both Kafka clusters; MirrorMaker 2 is not started yet.
3. `make test` — send 2,000,000 protobuf orders to `east`, then start MM2, validate all 2,000,000 mirrored orders on `west`, wait for the `dr-*` consumer checkpoint, switch that consumer group to `west`, and send and validate 2,000,000 new orders on `west`.
4. `make down` — remove both Kafka clusters, MirrorMaker 2, the operator, and the namespace.
5. `make kind-down` — delete kind and the example's local client images.

Prerequisites: Docker, `make`, `kind`, `kubectl`, Helm, Python 3, `envsubst`, and `grep`. Producer and consumer images build with Docker; host C++, protobuf, and Rust toolchains are not required.

Every run uses a unique topic such as `orders-20261114070120` and run ID, so it does not need to erase existing topic data. Override `COUNT`, `TOPIC`, or `RUN_ID` with Make variables. The C++ producer accepts counts up to 2,000,000. The default test validates 4,000,000 unique orders across the initial source batch and producer cutover batch, and also validates the mirrored copy of the initial batch.

Make exports the example-owned `results/kubeconfig` before kind creation and
removes it at `kind-down`; kubectl and Helm always specify the full kind context.
The default user context is not changed.

## Failure exercises

Run these after `make up`. Each one starts MM2, continuously produces the full configured workload while injecting the fault, then validates the replica and consumer offset checkpoint.

- `make chaos-mm2-worker` — delete one of the two MM2 worker pods after production starts. Kafka Connect rebalances the connector tasks; the other worker keeps the Connect cluster available. The test checks the producer, waits for both workers to return, checks synced group offsets, and validates every mirrored order.
- `make chaos-broker-disk-loss` — delete broker 3 on `west` while the source is producing and MM2 is writing to the target. Because the example uses ephemeral storage, replacing the pod also loses that broker's local Kafka data. Strimzi recreates the broker and Kafka restores replicas from the other brokers; the test checks offsets and validates the full mirrored workload. `make chaos-target-broker` is an alias.

The workload uses idempotent `acks=all` producer writes. MM2's source connector is configured for exactly-once source support and reads only committed records; the Rust validator uses `read_committed`. Group offsets are checkpointed every 5 seconds and applied on the target only while that target group is inactive. This is asynchronous: failover between checkpoints can replay records from the previous checkpoint. The test waits for a native checkpoint proof before promoting the target consumer.

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

**Still unqualified:** independent live readers throughout fault/recovery,
external per-ID fsynced producer acknowledgement histories, continuous error/
latency reports, native process fencing/survivor stability and complete
post-fault replica repair. The November 14 Pod deletion results below remain
historical and were not rerun as production proofs here. The alternate-policy
failures remain failed. No new throughput, availability or acknowledged-loss
claim is made from this checkpoint-only backfill.

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

## Verified run

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
| `jobs/` | Kubernetes Jobs for producer and consumer |
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
