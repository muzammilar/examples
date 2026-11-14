# Kafka examples

Website: [kafka.apache.org](https://kafka.apache.org/) | GitHub: [apache/kafka](https://github.com/apache/kafka)

| Example | What it covers |
|---|---|
| [`single-broker/`](single-broker/) | Legacy single-broker Kafka and ZooKeeper setup. |
| [`multi-broker/`](multi-broker/) | Legacy multi-broker Docker Compose setup. |
| [`vagrant-single-vm/`](vagrant-single-vm/) | Kafka on a single Vagrant VM. |
| [`strimzi-jbod/`](strimzi-jbod/) | Strimzi on kind; add a JBOD volume, rebalance replicas between disks, then safely remove it; C++ protobuf producer and Rust consumer. |
| [`connect-postgres-enrichment/`](connect-postgres-enrichment/) | Distributed Kafka Connect with Debezium PostgreSQL CDC, Go stream-table enrichment, and C++/Rust protobuf clients. |

## Benchmarks

No Kafka throughput benchmark has been run in this repository.

## Known issues

- The Strimzi JBOD walkthrough uses ephemeral volumes and demonstrates disk reassignment, not persistence or production disk performance.
- The Kafka Connect pipeline runs without TLS or SASL on a local Compose network and uses demo-only PostgreSQL credentials.
- Kafka Connect connector plugins use JVM APIs. The application join runs in a separate Go service; PostgreSQL CDC itself is handled by the Debezium connector.
