# Kafka on Strimzi — JBOD disk changes

A local kind cluster running Strimzi with three dedicated KRaft controllers and three Kafka brokers. The walkthrough starts with one log volume per broker, adds a second volume, moves replicas between the volumes with Cruise Control, then safely removes the added volume. A C++ protobuf producer and Rust consumer exercise the cluster while it runs.

This is a development example. Its ephemeral JBOD volumes model separate Kafka log directories but do not preserve data when a broker pod is recreated.

## Quick start

1. `make kind-up` — create the kind cluster.
2. `make up` — install Strimzi and wait for the Kafka cluster.
3. `make test` — build and run the C++ producer and Rust consumer (1,000,000 records by default; override with `make test COUNT=2000000`).
4. `make down` — remove the Kafka namespace and operator, leaving kind available.
5. `make kind-down` — delete kind and the example's local client images.

Prerequisites: Docker, `make`, `kind`, `kubectl`, Helm, `envsubst`, `grep`, and `awk`. The images build inside Docker, so host C++ and Rust toolchains are not required.

## JBOD disk exercise

Run these after `make up` and before cleanup. Inspect each `ProposalReady` plan before approving it.

1. `make disk-add` — add volume 1 to every Kafka broker.
2. `make disk-rebalance-plan` — request a disk rebalance and show the proposal.
3. `make disk-approve` — approve and wait for replicas to move across the volumes.
4. `make disk-remove-plan` — request removal of volume 1 and show the proposal.
5. `make disk-approve` — approve the move off volume 1.
6. `make disk-remove-finish` — shrink the KafkaNodePool back to one volume.

Use `make status`, `make cli`, and `make logs` to inspect the cluster.

`disk-approve` intentionally approves the currently pending `KafkaRebalance` after you inspect its proposal. Run it only after a `ProposalReady` status is visible. The remove plan targets volume 1 on all brokers; do not remove a volume while it still hosts replicas.

## What it demonstrates

- Strimzi 1.3.1 with Kafka 4.3.2 in KRaft mode and Strimzi's `KafkaNodePool` API.
- Three dedicated controller nodes and three broker nodes; broker IDs 3–5 use replication factor 3 and `min.insync.replicas=2`.
- Adding a JBOD volume does not itself move existing replicas. `KafkaRebalance` with `rebalanceDisk: true` asks Cruise Control to balance data between disks.
- `remove-disks` moves replicas off selected volume IDs before the storage manifest is changed. Kafka requires this ordering to avoid data loss.
- A C++ client serializes `Order` protobuf messages and produces with idempotence and `acks=all`; a Rust consumer decodes, validates, and commits records after processing.

The cluster uses ephemeral storage to keep the local demo self-contained. Strimzi recommends block storage and treats ephemeral storage as development/test storage; this setup does not model production durability or disk performance.

## Verified run

On 2026-11-14, the default `make test` reset the `orders` topic, built both client images, delivered 1,000,000 protobuf orders, and validated 1,000,000 unique orders in the Rust consumer. Their amount sum was 3,499,500,000 cents. This is a correctness/load-size check, not a throughput benchmark. A separate earlier 100-order disk walkthrough added volume 1 to brokers 3–5, completed 9 intra-broker movements for the rebalance proposal and 189 movements for the remove-disks proposal, then returned all 3 brokers to one volume; Cruise Control reported 0 MB to move in both plans. Those disk-movement figures are not measurements from the million-order run.

## Setup

| Component | Version | Role |
|---|---|---|
| kind nodes | `kindest/node:v1.34.0` | One control plane and three workers |
| Strimzi | `1.3.1` | Kafka operator and Cruise Control |
| Kafka | `4.3.2` | Three KRaft controllers plus three brokers; brokers have two JBOD log directories during the disk-change walkthrough |
| C++ producer | CMake, Debian Bookworm librdkafka / protobuf packages | Idempotent protobuf producer, compiled into the image |
| Rust consumer | `rdkafka 0.39.0`, `prost 0.14.1` | Decode and validate protobuf orders |

The selected Strimzi release supports Kafka 4.3 and requires Kubernetes 1.32 or newer. The kind node image is pinned above that minimum.

## Layout

| Path | Purpose |
|---|---|
| `manifests/` | Strimzi Kafka, one-volume/two-volume pools, topic, and rebalance resources |
| `producer/` | C++ producer and protobuf schema; `protoc` runs in the image build |
| `consumer/` | Rust consumer and protobuf generation |
| `jobs/` | Kubernetes Jobs for the one-shot producer and consumer |

## Links

- [Strimzi JBOD storage and disk rebalancing](https://strimzi.io/docs/operators/latest/full/deploying.html)
- [Strimzi 1.3.1 source](https://github.com/strimzi/strimzi-kafka-operator/tree/1.3.1)
- [Apache Kafka 4.3 disk and replica reassignment operations](https://kafka.apache.org/43/operations/basic-kafka-operations/)
- [Apache Kafka 4.3.2 source](https://github.com/apache/kafka/tree/4.3.2)
- [Strimzi 1.3.1 release](https://github.com/strimzi/strimzi-kafka-operator/releases/tag/1.3.1)
- [librdkafka 2.0.2 C++ API and producer guarantees](https://github.com/confluentinc/librdkafka/blob/v2.0.2/INTRODUCTION.md)
- [rust-rdkafka documentation](https://docs.rs/rdkafka/0.39.0/rdkafka/)
- [rust-rdkafka 0.39.0 source](https://github.com/fede1024/rust-rdkafka/tree/v0.39.0)
