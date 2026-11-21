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

At the 2026-11-20 pause, the broker experiment was **stopped during final replay**
and the other fault cases and benchmark trials were unrun. The follow-up below
closes those core cases. Extended disk, partition, scaling, pressure, upgrade and
restore cases remain unrun.

## Recovery and diagnostic trials — 2026-11-21

[Committed results](VERIFIED_RESULTS.json) retain counts, latency distributions,
phase markers, resource/storage measurements, image/source hashes and original
failed verdicts. The existing ten service containers and volumes were resumed;
no failed dataset was reset. Every new case kept independent writers/readers and
SQL probes live through the operation and native repair, then replayed every
current and earlier acknowledged order body, key and partition on both topics.

| Fresh fault case | Source ACKs | Callback p50 / p99 ms | Enriched creation-to-observation p50 / p99 ms | ACK loss / corruption |
|---|---:|---:|---:|---:|
| Broker SIGKILL; repaired broker promoted to lead all 12 input/output partitions | 2,009,216 | 41.828 / 1271.923 | 28856.776 / 39775.313 | 0 / 0 |
| Active Connect worker SIGKILL; surviving worker takes over CDC task | 2,001,024 | 27.791 / 704.158 | 9402.412 / 20396.451 | 0 / 0 |
| Go processor SIGKILL; retained transactional group resumes | 2,004,096 | 49.916 / 699.223 | 9977.676 / 24362.953 | 0 / 0 |
| PostgreSQL SIGKILL; cached enrichment continues and CDC catches up | 2,006,144 | 60.520 / 654.242 | 8298.486 / 21753.101 | 0 / 0 |

The original interrupted broker case separately reconciled **2,001,024 ACKs** on
both paths; its original verdict remains failed/interrupted. Including that
history, the earlier baseline and the three trials below, the final replay covers
**18,038,912 ACKs across nine data histories**, with zero acknowledged loss or
corruption. The RF1 setup failure remains failed and has zero order records. The
known one-record stale-CDC negative control is preserved separately and excluded
from this passing cohort. All **1,161 acknowledged SQL probe operations** reconcile;
ambiguous outcomes remain distinct from ACKs in the JSON reports.

### Three healthy minimum-count trials

Measured on **Apple M4 Pro / 48 GiB host RAM**, Docker Desktop **29.5.3**, native
aarch64 Linux **6.12.76-linuxkit**, **11 vCPUs / 24.42 GiB VM RAM**. Six Kafka
processes each have **1 CPU / 1,536 MiB**, two Connect workers each **1 CPU / 1 GiB**,
PostgreSQL **1 CPU / 512 MiB**, Go **1 CPU / 256 MiB**, producer **1 CPU / 512 MiB**,
each independent reader **1 CPU / 1 GiB**, and each replay verifier **2 CPUs / 2 GiB**.
This is one shared host, not independent machines or a capacity comparison.
Images were prebuilt; Docker builds and the initial retained-cluster restart are
outside these trial windows. Each trial includes Go cache rebuild/setup. Witness
producer/readers use librdkafka **2.12.1**; the Go processor uses franz-go **1.22.3**.

Each trial uses a fresh Go process/topic/group, six partitions, RF3/min-ISR2 and
**10,000 customers**. Source orders contain unique run/order IDs, customer IDs,
integer amounts and creation timestamps; output adds the cached customer fields.
Trials 1–2 contain **144,815,284 input / 228,456,286 output protobuf bytes** each;
trial 3 contains **145,338,548 / 229,280,608 bytes**. UUIDs/timestamps differ; the
generation pattern and client budgets are unchanged. In-flight drain can exceed
the two-million minimum, so actual ACK counts are reported.

| Trial | ACKs | First intent → last ACK / last enriched observation s | ACK / pipeline records/s | Callback mean / p50 / p99 ms | Enriched p50 / p99 ms |
|---|---:|---:|---:|---:|---:|
| 1 | 2,002,048 | 37.746 / 56.789 | 53040.49 / 35253.99 | 88.141 / 56.293 / 698.295 | 13267.920 / 26535.850 |
| 2 | 2,002,048 | 38.810 / 57.881 | 51586.12 / 34589.33 | 100.927 / 69.574 / 774.035 | 14796.749 / 32073.589 |
| 3 | 2,009,216 | 39.243 / 59.766 | 51199.91 / 33617.91 | 98.660 / 65.603 / 758.630 | 16606.815 / 27064.930 |

Median ACK rate: **51,586.12 records/s** (range **51,199.91–53,040.49 records/s**,
IQR **920.29 records/s**).
Median pipeline observation rate: **34,589.33 records/s** (range
**33,617.91–35,253.99 records/s**, IQR **818.04 records/s**). IQR uses inclusive
linear interpolation across three trial rates. JSON includes mean/p50/p95/p99/p99.9
and sample counts. Callback timing includes SDK queues/retries; observation latency
includes encoding, processing, buffering and fsynced reader journals. These are
different timing boundaries, not language performance comparisons.

| Trial | Observed container / host-controller CPU core-s | Largest sampled individual-container charged memory MiB | Persistent allocated storage before → last sample MiB | Cgroup block read / write MiB | Source / enriched record-section compression ratio |
|---|---:|---:|---:|---:|---:|
| 1 | 505.555 / 84.247 | 1376.379 | 2231.609 → 2575.809 | 0.004 / 370.512 | 5.764 / 4.772 |
| 2 | 474.086 / 86.022 | 1500.543 | 2576.129 → 2919.996 | 0.016 / 370.312 | 5.775 / 4.771 |
| 3 | 513.361 / 87.354 | 1535.895 | 2920.348 → 3265.441 | 0.117 / 373.914 | 5.773 / 4.774 |

Resource costs cover the **whole experiment**, including native inspection and
growing historical replay work; the request/pipeline windows above are shorter.
Raw samples target five-second cadence. JSON separates roles, CPU utilization
(one core = 100%), memory sample means/maxima and read/write byte rates. Memory
is cgroup charged usage, not heap, peak RSS or a simultaneous whole-stack peak.
Host CPU excludes child CLI processes, which are recorded separately; VM/daemon
overhead is excluded. Disk snapshots cover all owned persistent volumes, retained
topics/replicas, WAL and metadata; phase samples can miss transient peaks.

Compression was measured **after** the trials from every source/output partition
on one repaired replica: CRC32C-verified Kafka magic-2 batches, decoded with pinned
LZ4/CRC libraries using the [documented record format](https://kafka.apache.org/43/implementation/message-format/)
and [Kafka 4.3.2 source](https://github.com/apache/kafka/blob/4.3.2/clients/src/main/java/org/apache/kafka/common/record/internal/DefaultRecordBatch.java).
Ratios are uncompressed/compressed record-section bytes for the same stored data;
batch headers, indexes and RF3 overhead are separate. Physical output had **19
additional data records** in the processor-crash case and **45** in the original
interrupted broker case; they were not visible to strict read-committed replay.
Physical counts therefore do not establish duplicate committed processing. The
inspector does not independently classify individual transaction outcomes.

These are short pipeline diagnostics: no discarded 60-second warmup, sustained
five-minute measurement or controlled cold-cache state. The customer cache is
loaded before orders; replaying older data does not prove cold reads. Runtime
block I/O accounting does not provide cache-hit rates; this API exposed no IOPS,
I/O latency or queue depth. Customer display fields stay fixed during traffic
while CDC probe versions change; no temporal-join/linearizability claim is made.
Partitions/quorum loss, disk replacement, scaling, upgrades, pressure, independent
restore, batch/skew/cold-read matrices and competing-system comparisons remain
unrun. Eight host controller tests pass; the compression inspector also rejected
corrupted-CRC and truncated native segments.

The [experimental witness harness](https://github.com/muzammilar/examples/tree/kafka-connect-live-recovery/kafka/connect-postgres-enrichment)
produced these results. Clean-checkout initialization and its full production-test
lifecycle remain unqualified; these case results do not establish those paths.
At the user's request, large journals and copied log segments are cleaned after
publication. Reports, metric samples and proof summaries remain locally; hashes
cannot replay deleted raw events. New full reproduction requires a fresh dataset.

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
