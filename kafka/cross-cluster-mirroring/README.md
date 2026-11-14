# Kafka — cross-cluster mirroring with MirrorMaker 2

Two independent Strimzi-managed KRaft Kafka clusters run in one local kind cluster. `east` starts as the active cluster and accumulates the workload before Apache Kafka MirrorMaker 2 (MM2) is deployed. MM2 then mirrors to the standby `west` cluster. The test switches the consumer group to `west` at its checkpointed offset, then switches the producer to `west` and validates the new batch there.

## Quick start

1. `make kind-up` — create the kind cluster.
2. `make up` — install Strimzi and start both Kafka clusters; MirrorMaker 2 is not started yet.
3. `make test` — send 2,000,000 protobuf orders to `east`, then start MM2, validate all 2,000,000 mirrored orders on `west`, wait for the `dr-*` consumer checkpoint, switch that consumer group to `west`, and send and validate 2,000,000 new orders on `west`.
4. `make down` — remove both Kafka clusters, MirrorMaker 2, the operator, and the namespace.
5. `make kind-down` — delete kind and the example's local client images.

Prerequisites: Docker, `make`, `kind`, `kubectl`, Helm, `envsubst`, `grep`, and `awk`. Producer and consumer images build with Docker; host C++, protobuf, and Rust toolchains are not required.

Every run uses a unique topic such as `orders-20261114070120` and run ID, so it does not need to erase existing topic data. Override `COUNT`, `TOPIC`, or `RUN_ID` with Make variables. The C++ producer accepts counts up to 2,000,000. The default test validates 4,000,000 unique orders across the initial source batch and producer cutover batch, and also validates the mirrored copy of the initial batch.

## Failure exercises

Run these after `make up`. Each one starts MM2, continuously produces the full configured workload while injecting the fault, then validates the replica and consumer offset checkpoint.

- `make chaos-mm2-worker` — delete one of the two MM2 worker pods after production starts. Kafka Connect rebalances the connector tasks; the other worker keeps the Connect cluster available. The test checks the producer, waits for both workers to return, checks synced group offsets, and validates every mirrored order.
- `make chaos-broker-disk-loss` — delete broker 3 on `west` while the source is producing and MM2 is writing to the target. Because the example uses ephemeral storage, replacing the pod also loses that broker's local Kafka data. Strimzi recreates the broker and Kafka restores replicas from the other brokers; the test checks offsets and validates the full mirrored workload. `make chaos-target-broker` is an alias.

The workload uses idempotent `acks=all` producer writes. MM2's source connector is configured for exactly-once source support and reads only committed records; the Rust validator uses `read_committed`. Group offsets are checkpointed every 5 seconds and applied on the target only while that target group is inactive. This is asynchronous: failover between checkpoints can replay records from the previous checkpoint. The test waits for the checkpoint before checking the target offsets.

## What it demonstrates

- Separate KRaft metadata quorums and broker pools for `east` and `west`, each with three controllers and three brokers.
- Strimzi's `KafkaMirrorMaker2` resource running Kafka's MM2 source and checkpoint connectors. MM2 uses the target cluster for its Kafka Connect config, offset, and status topics.
- MM2 uses `IdentityReplicationPolicy` so the promoted `west` cluster keeps the same topic name and can accept the producer after cutover. It mirrors one way only; do not configure both clusters as identity-policy sources, since that can create replication loops.
- MM2 copies the protobuf key, value, headers, and partition number; the Rust consumer decodes the original protobuf without a schema registry or transformation.
- `offset-syncs` map source record offsets to target record offsets. The checkpoint connector translates the `dr-*` consumer group's committed offsets for the identically named topic. The target consumer starts at that checkpoint before the target producer writes its new batch.
- Exactly-once source support is explicit: the Connect worker enables it, the MM2 source connector requires it, and its source consumer uses `read_committed`. This behavior applies to MM2's source-to-target Kafka writes; it is not a transaction spanning both clusters or application side effects.

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

Use `make status`, `make logs`, and `make logs-mm2` to inspect the two clusters and replication workers. The kind context is always `kind-kafka-cross-cluster-mirroring`.

## Links

- [Strimzi 1.3.1 MirrorMaker 2 configuration](https://strimzi.io/docs/operators/1.3.1/deploying#assembly-mirrormaker2-str)
- [Apache Kafka 4.3 MirrorMaker configuration](https://kafka.apache.org/43/configuration/mirrormaker-configs/)
- [Apache Kafka 4.3.2 MirrorSourceTask](https://github.com/apache/kafka/blob/4.3.2/connect/mirror/src/main/java/org/apache/kafka/connect/mirror/MirrorSourceTask.java)
- [Apache Kafka 4.3.2 MirrorSourceConnector](https://github.com/apache/kafka/blob/4.3.2/connect/mirror/src/main/java/org/apache/kafka/connect/mirror/MirrorSourceConnector.java)
- [Apache Kafka 4.3.2 MirrorCheckpointTask](https://github.com/apache/kafka/blob/4.3.2/connect/mirror/src/main/java/org/apache/kafka/connect/mirror/MirrorCheckpointTask.java)
- [Confluent Cluster Linking](https://docs.confluent.io/platform/current/multi-dc-deployments/cluster-linking/index.html)
