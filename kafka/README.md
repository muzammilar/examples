# Kafka examples

Website: [kafka.apache.org](https://kafka.apache.org/) | GitHub: [apache/kafka](https://github.com/apache/kafka)

| Example | What it covers |
|---|---|
| [`single-broker/`](single-broker/) | Legacy single-broker Kafka and ZooKeeper setup. |
| [`multi-broker/`](multi-broker/) | Legacy multi-broker Docker Compose setup. |
| [`vagrant-single-vm/`](vagrant-single-vm/) | Kafka on a single Vagrant VM. |
| [`strimzi-jbod/`](strimzi-jbod/) | Strimzi on kind; add a JBOD volume, rebalance replicas between disks, then safely remove it; C++ protobuf producer and Rust consumer. |
| [`connect-postgres-enrichment/`](connect-postgres-enrichment/) | Distributed Kafka Connect with Debezium PostgreSQL CDC, Go stream-table enrichment, and C++/Rust protobuf clients. |
| [`cross-cluster-mirroring/`](cross-cluster-mirroring/) | Two Strimzi KRaft clusters with Apache MirrorMaker 2, offset checkpoints, and producer-under-failure recovery tests. |

## Benchmarks

| Example | Date / scope | Result |
|---|---|---|
| [Strimzi JBOD](strimzi-jbod/#benchmark) | 2026-11-19, one-host kind, 3 fresh C++/Rust workload processes, at least 2 million ACKs/trial | Median 33,352.06 ACKs/s, 2.209 MiB/s; witness/validation costs included, warm retained Kafka, not a saturation or competing-product comparison |

The Strimzi serial correctness/broker/controller/directory-write-failure aggregate
reconciled 4,004,096 new ACKs without loss/corruption at a 300-second delivery
budget. A separate 10-second-deadline disk run remains failed: 10,427 extra physical
copies among ambiguous sends, with zero lost/corrupt/duplicated ACKs. Its complete
binary records and independent fingerprints are preserved. See the example for
resource budgets, latency boundaries and unrun operator cases.

## Known issues

- Strimzi uses ephemeral EmptyDir volumes: Pod recreation loses local copies, container restart retains them. Directory write denial is not physical device failure. The short-deadline duplicate boundary above remains a failed experiment with an unisolated root cause.
- The Kafka Connect pipeline runs without TLS or SASL on a local Compose network and uses demo-only PostgreSQL credentials.
- Kafka Connect connector plugins use JVM APIs. The application join runs in a separate Go service; PostgreSQL CDC itself is handled by the Debezium connector.
