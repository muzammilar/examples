# Kafka Connect — PostgreSQL customer enrichment

A local three-broker Kafka cluster, a two-worker distributed Kafka Connect cluster, PostgreSQL, and a Go enrichment processor. The C++ producer emits protobuf orders to `orders`; Debezium PostgreSQL source connector publishes the customer table's snapshot and changes to a compacted topic; the Go processor joins each order with that Kafka-backed customer state and writes an enriched protobuf to `orders.enriched`; the Rust consumer validates the output.

Kafka Connect connector plugins implement JVM APIs, so a Go program cannot run as a connector or Single Message Transform inside a Connect worker. This example uses the supported Java Debezium plugin for PostgreSQL CDC and keeps application logic in Go with `franz-go`. The Go join is fed by PostgreSQL CDC rather than issuing one database query per order, avoiding an N+1 lookup path and making database changes visible as a Kafka stream.

## Quick start

1. `make docker-up` — start Kafka, PostgreSQL, and the Connect workers.
2. `make up` — create topics, register Debezium, and wait for the Go processor.
3. `make test` — produce 2,000,000 protobuf orders by default and check every enriched output (`make test COUNT=3000000` changes the workload; the producer allows up to 5,000,000).
4. `make down` — stop and remove the Go processor container while keeping the services and data available.
5. `make docker-down` — stop the Compose stack and remove its volumes and local images.

Prerequisites: Docker with the Compose v2 plugin, `make`, `curl`, `jq`, and `seq`. C++, Go, Rust, and `protoc` toolchains run in the image builds.

Use `make status`, `make logs`, and `make cli` for inspection and database access. `make demo` reruns the producer/consumer flow without rebuilding the infrastructure.

`make up` can also be run directly; it starts the Compose infrastructure before provisioning the example. `make demo` runs the same producer/consumer flow as `make test`.

## What it demonstrates

- Kafka Connect distributed mode with shared, replicated config, offset, and status topics.
- Debezium's PostgreSQL source connector, which snapshots the customer table and then streams committed inserts, updates, and deletes using PostgreSQL logical decoding (`pgoutput`).
- A compacted customer topic keyed by customer ID, used as the Go processor's restartable lookup state.
- `franz-go` consumer-group transactions: output records and consumed order offsets commit atomically to Kafka. The PostgreSQL source is read-only from the application's perspective; no external database side effect participates in that Kafka transaction.
- Protobuf as the application event contract between C++, Go, and Rust. The Connect CDC topic uses Debezium's JSON envelope and is decoded by the Go processor.

The Compose setup is local development infrastructure. Kafka has three brokers and three KRaft controllers, but it has no TLS or SASL and is not a production security configuration. The demo binds host access to loopback and keeps services on a private Compose network.

## Verified run

On 2026-11-14, the default `make test` built the C++ producer and Rust consumer images, sent 2,000,000 protobuf orders, and validated 2,000,000 unique enriched protobuf orders. PostgreSQL contained 10,000 customer rows; the Go processor log confirmed it loaded all 10,000 from the Debezium CDC topic before processing orders. These counts verify the end-to-end join and republish path; they are not throughput measurements. Set `COUNT` to change the message count and `CUSTOMER_COUNT` to change the PostgreSQL seed size and matching application configuration.

## CDC fix and native qualification — 2026-11-20

The original processor stopped polling customer changes after loading its seed.
A SQL-acknowledged update was visible in the CDC topic, but a later enriched order
still used the old name after **7.291 s**. The failed observation is retained.
The processor now keeps CDC consumption active, safely clones protobuf lookup
values, bounds transactions to 5,000 input records, preserves input partition IDs,
and exposes `/status` with CDC positions and committed-order counters. Input and
output topics must have matching partition counts (six in this example).

The pinned Debezium image has a mixed environment interface: internal topic RF
settings require `CONNECT_CONFIG_STORAGE_REPLICATION_FACTOR`,
`CONNECT_OFFSET_STORAGE_REPLICATION_FACTOR` and
`CONNECT_STATUS_STORAGE_REPLICATION_FACTOR`; advertised identity uses
`ADVERTISED_HOST_NAME`, and flush timing uses `OFFSET_FLUSH_INTERVAL_MS`.
The old RF variables were ignored and actual internal topics had **one replica**.
Existing replicas were expanded natively without resetting state; both generated
worker configurations and actual RF3/ISR3/zero-lag state were checked. New setups
request RF3 from creation. The configurable 10-second rebalance grace is a local
lab setting; it does not weaken replication or durability.

The [recovery branch's independent witness baseline](https://github.com/muzammilar/examples/blob/kafka-connect-live-recovery/kafka/connect-postgres-enrichment/RECOVERY_TEST_STATUS.md)
verified **2,004,096 ACKs / 10,000 customers**, every source/enriched order body,
key and partition, and **zero acknowledged loss/corruption**. Serialized input:
**144,964,788 bytes**; enriched output: **228,691,806 bytes**. Callback p50/p99:
**18.874/835.920 ms**; enriched creation-to-observation p50/p99:
**12,410.462/34,828.717 ms** (2,004,096 successful observations each).
Those include SDK/witness queues and reader backlog. The mixed rate-limited/final
burst ACK window was **22,921.20 ACK/s**, not a capacity benchmark. Resource
sampling counted **448.620 CPU core-seconds** across the observed experiment,
including native inspection and full verification. Largest sampled individual-container charged/anonymous memory was **1,153,605,632 / 881,565,696 bytes** (the replay verifier); these are sample maxima, not process peak RSS or a simultaneous stack peak. Three benchmark trials have
not run. Docker builds passed Go vet and race tests; the baseline also used C++
CTest and eight Rust observer tests. Tested HTTP ports were 18083/18080.

The broker recovery experiment was **stopped by the user during final replay**
and is unqualified. Connect-worker, processor and PostgreSQL fault cases remain
unrun; so do disk replacement, partitions, scaling, pressure, upgrades and restore.
The paused harness and its resume instructions remain on `kafka-connect-live-recovery`;
the normal five-command workflow above remains the simple walkthrough.

## Setup

| Service | Image / version | Host port | Role |
|---|---|---:|---|
| Kafka brokers and controllers | `apache/kafka:4.3.2` | `29092`, `39092`, `49092` (override with `KAFKA_BROKER_n_PORT`) | Three brokers and a three-node KRaft controller quorum |
| Kafka Connect | `quay.io/debezium/connect:3.7.1.Final` | `8083` (override with `CONNECT_PORT`) | Two distributed workers with Debezium PostgreSQL plugin |
| PostgreSQL | `postgres:18.7` | `55432` (override with `POSTGRES_PORT`) | Logical replication source database |
| Go processor | Go `1.27.4`, `franz-go 1.22.3` | `8080` (override with `PROCESSOR_PORT`) | CDC cache, transactional order enrichment, readiness endpoint |
| C++ producer / Rust consumer | CMake / Cargo; built from local Dockerfiles | — | Protobuf application clients; started by `make demo` |

The Go service rebuilds its in-memory lookup from the compacted CDC topic at startup and continues applying changes after readiness. Restarting it rebuilds the cache before processing orders. Readiness marks the configured seed population, not a transactionally consistent PostgreSQL snapshot. Joins use the latest customer value observed by this process; freshness is asynchronous. Cached joins can continue during source outages. The default 10,000-row seed exercises a larger in-memory lookup set; it does not implement a general-purpose persistent state store or handle schema evolution. PostgreSQL credentials are demo-only values in Compose and are not suitable for a shared or production environment.

## Layout

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Kafka, Connect workers, PostgreSQL, and application images |
| `postgres/init.sh`, `postgres/init.sql` | Logical-replication role, schema, and configurable customer seed (10,000 by default) |
| `connectors/postgres-customers.json` | Debezium PostgreSQL connector configuration |
| `proto/order.proto` | Shared order, customer, and enriched-order protobuf contract |
| `producer/`, `processor/`, `consumer/` | C++, Go, and Rust applications |

## Links

- [Apache Kafka 4.3 Connect user guide](https://kafka.apache.org/43/kafka-connect/user-guide/)
- [Apache Kafka 4.3 connector development guide](https://kafka.apache.org/43/kafka-connect/connector-development-guide/)
- [Debezium 3.7 PostgreSQL connector guide](https://debezium.io/documentation/reference/3.7/connectors/postgresql.html)
- [Debezium 3.7.1 PostgreSQL connector source](https://github.com/debezium/debezium/blob/3.7.1.Final/debezium-connector-postgres/src/main/java/io/debezium/connector/postgresql/PostgresConnector.java)
- [Debezium 3.7.1 release notes and tested Kafka compatibility](https://debezium.io/releases/3.7/release-notes)
- [Kafka 4.3.2 JVM connector API source](https://github.com/apache/kafka/blob/4.3.2/connect/api/src/main/java/org/apache/kafka/connect/connector/Connector.java)
- [franz-go 1.22.3 transaction guide](https://github.com/twmb/franz-go/blob/v1.22.3/docs/transactions.md)
- [franz-go 1.22.3 `GroupTransactSession` source](https://github.com/twmb/franz-go/blob/v1.22.3/pkg/kgo/txn.go)
- [librdkafka 2.0.2 C++ API](https://github.com/confluentinc/librdkafka/blob/v2.0.2/INTRODUCTION.md)
- [Protocol Buffers language guide](https://protobuf.dev/programming-guides/proto3/)
- [protobuf-go 1.36.12 runtime source](https://github.com/protocolbuffers/protobuf-go/tree/v1.36.12)
- [rust-rdkafka documentation](https://docs.rs/rdkafka/0.39.0/rdkafka/)
- [rust-rdkafka 0.39.0 source](https://github.com/fede1024/rust-rdkafka/tree/v0.39.0)
