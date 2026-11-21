# Kafka Connect — PostgreSQL customer enrichment

A local three-broker Kafka cluster, a two-worker distributed Kafka Connect cluster, PostgreSQL, and a Go enrichment processor. The C++ producer emits protobuf orders to `orders`; Debezium PostgreSQL source connector publishes the customer table's snapshot and changes to a compacted topic; the Go processor joins each order with that Kafka-backed customer state and writes an enriched protobuf to `orders.enriched`; the Rust consumer validates the output.

Kafka Connect connector plugins implement JVM APIs, so a Go program cannot run as a connector or Single Message Transform inside a Connect worker. This example uses the supported Java Debezium plugin for PostgreSQL CDC and keeps application logic in Go with `franz-go`. The Go join is fed by PostgreSQL CDC rather than issuing one database query per order, avoiding an N+1 lookup path and making database changes visible as a Kafka stream.

## Quick start

1. `make docker-up` — start Kafka, PostgreSQL, and the Connect workers.
2. `make up` — create topics, register Debezium, and wait for the Go processor.
3. `make test` — run the witnessed baseline with at least 2,000,000 protobuf orders and replay every acknowledged source/enriched body, key and partition (`COUNT=1000000` selects one million).
4. `make down` — reconcile retained histories, then stop and remove the Go processor while keeping infrastructure and data.
5. `make docker-down` — reconcile again, stop the stack, and remove its owned volumes and built workload images; preserve external evidence.

Prerequisites: Docker with Compose, `make`, and Python 3.11+. C++, Go, Rust, and `protoc` toolchains run in the image builds.

Use `make status`, `make logs`, and `make cli` for inspection and database access. `make demo` reruns the producer/consumer flow without rebuilding the infrastructure.

`make up` also works directly. It prepares infrastructure, builds the C++/Rust
witness workloads and Go processor, and configures topics/CDC. `make demo` is an
alias for `make test`. The recovery harness accepts **1–2 million** minimum orders
and **10,000 customers**; completed delivery batches may slightly exceed `COUNT`.
The older simple producer/consumer remain available as Compose `apps` services.

The lifecycle saves its owner and chosen loopback ports in ignored `results/`.
Override ports on the first `make docker-up`, for example `CONNECT_PORT=18083
PROCESSOR_PORT=18080`; later commands reuse those saved ports. Reapplying `make up`
preserves the current processor topics, group and transaction identity. Run repeat
setup as a separate Make invocation. `make cli` opens an interactive PostgreSQL
session using the saved configuration.

One lock serializes lifecycle and experiment commands. An unqualified failed
history blocks destructive cleanup until separately diagnosed/reconciled. Teardown
saves stopped-process inspection, checks for clean exits, and removes anonymous
volumes only after a healthy runtime listing proves no container references them.
The next setup archives a closed fixture under `results/archives/<owner>/`, keeping
its failed verdicts and evidence separate from the fresh fixture. It refuses to
adopt an unowned existing project or start over remaining owned storage.
The cleanup checks supplement [Compose's documented `down` behavior](https://docs.docker.com/reference/cli/docker/compose/down/)
and its [v5.1.4 removal implementation](https://github.com/docker/compose/blob/v5.1.4/pkg/compose/down.go),
which can report an in-use resource as a warning rather than a failed command.

## Operational commands

| Command | Scope |
|---|---|
| `make test-production` | Serial baseline, broker, active Connect worker, processor and PostgreSQL crash cases; each replays current/prior ACK histories |
| `make chaos-broker` / `make chaos-connect` | One native role-aware crash and recovery case |
| `make chaos-processor` / `make chaos-postgres` | One application/CDC dependency crash and recovery case |
| `make benchmark` | Three fresh minimum-count diagnostic cases; not a sustained capacity benchmark |
| `make verify-history` | Read-only strict source/output replay and SQL witness reconciliation |
| `make reconcile-prior CASE_ID=<id>` | Separate data reconciliation for an interrupted case; keeps its failed verdict |
| `make workload-images` | Explicitly rebuild local workload images; normal `up` reuses matching source/image receipts |

All load cases use `COUNT=2000000` by default, allowing **1–2 million** minimum
orders. Keep operational cases serial. Extended partition/disk/quorum/scaling,
pressure, upgrade and independent restore cases are not implemented by these targets.

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

The [independent witness baseline](RECOVERY_TEST_STATUS.md)
verified **2,004,096 ACKs / 10,000 customers**, every source/enriched order body,
key and partition, and **zero acknowledged loss/corruption**. Serialized input:
**144,964,788 bytes**; enriched output: **228,691,806 bytes**. Callback p50/p99:
**18.874/835.920 ms**; enriched creation-to-observation p50/p99:
**12,410.462/34,828.717 ms** (2,004,096 successful observations each).
Those include SDK/witness queues and reader backlog. The mixed rate-limited/final
burst ACK window was **22,921.20 ACK/s**, not a capacity benchmark. Resource
sampling counted **448.620 CPU core-seconds** across the observed experiment,
including native inspection and full verification. Largest sampled individual-container charged/anonymous memory was **1,153,605,632 / 881,565,696 bytes** (the replay verifier); these are sample maxima, not process peak RSS or a simultaneous stack peak. Three benchmark trials have
not run at that pause. Docker builds passed Go vet and race tests; the baseline also used C++
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
on one in-sync replica after native repair: CRC32C-verified Kafka magic-2 batches, decoded with pinned
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

The local witness harness produced these results. Its subsequent lifecycle
qualification is recorded below; the individual core cases do not establish the
extended production-test matrix. After publishing the original results, cleanup removed **129 owned containers / 25 owned
volumes**, with **nine unrelated containers / ten unrelated volumes unchanged**.
All example-built image tags are gone. Compose left **six unused anonymous Connect
volumes**; the initial teardown post-check failed. A separate scoped cleanup used
the saved mount identities and healthy empty-reference listings to remove exactly
those six volumes. The original failed teardown log remains preserved.

At the user's request, **145 large journal/log-copy paths** were deleted after
publication, reclaiming **5.48 GiB allocated**. That cleanup left **60.3 MiB** from this cohort:
reports, SQL journals, metric samples, failure records and proof summaries remain.
Hashes cannot replay deleted raw events, and native topics/volumes no longer exist.
New full reproduction uses a fresh dataset; the original control survives under
`results/archives/757176bf1c4b4340aa567867c271f156/` as local evidence rather than a
live Kafka topic.

## Recovery-harness lifecycle — 2026-11-21

The final corrected revision also passed **all five commands serially without
retries on empty owned infrastructure**, using `make test COUNT=1000000`:
**1,001,024 ACK orders**, zero acknowledged loss/corruption, followed by verified
application removal and complete owned-resource cleanup. This was an independent
fixture; the larger retained-state qualification below was not reused as its data.

[Lifecycle results](LIFECYCLE_RESULTS.json) record a fresh **2,001,024-ACK** baseline
with **10,000 customers / 104 SQL probe ACKs**, zero acknowledged loss/corruption,
and retained-state replay after setup, application removal/recreation and native
service stop/start. Corrected repeated setup retained all **10 service IDs/start
times**. Application recreation restored the exact topics, consumer group and
transaction identity while leaving the other **nine services unchanged**.

The prototype exposed four failed checks: an unnecessary rebuild replaced the Go
container, removed application configuration fell back to demo defaults, and two
shutdown checks rejected normal JVM SIGTERM exits or expected an obsolete broker
message. These attempts remain failed evidence. The final checks use source/image
hash receipts, saved processor configuration and current-incarnation native
shutdown logs. **23 controller tests pass**. No failed data was reset. Final
teardown removed **25 owned volumes**, all owned containers/networks and built
image tags, preserving **nine foreign containers / ten foreign volumes**.
The role-specific shutdown checks follow Kafka 4.3.2's
[broker](https://github.com/apache/kafka/blob/4.3.2/core/src/main/scala/kafka/server/BrokerServer.scala),
[controller](https://github.com/apache/kafka/blob/4.3.2/core/src/main/scala/kafka/server/ControllerServer.scala)
and [Raft server](https://github.com/apache/kafka/blob/4.3.2/core/src/main/scala/kafka/server/KafkaRaftServer.scala)
paths; exit 143 alone is insufficient.

During the baseline and its original verification, observed container CPU deltas
totaled **326.816 core-seconds**. The largest sampled individual-container charged
memory was **1,169,002,496 bytes** (Rust verifier); this is not a simultaneous stack
peak. Owned allocated storage snapshots grew from **95,592,448** to
**466,477,056 bytes**. Exposed block counters covered **12/16 container incarnations**:
observed reads/writes were **6,037,504 / 390,254,592 bytes**; missing counters are
unknown, and IOPS/I/O latency/queue depth were unavailable. These resource counters
exclude later lifecycle replays, image builds and teardown.

CRC-verified copies of all twelve source/output partitions contained exactly
**2,001,024 data records per side**. Source LZ4 record sections occupied
**45,502,053 bytes** from **255,192,651 uncompressed bytes**; output occupied
**71,348,613 bytes** from **339,223,142 bytes**, excluding headers/control records,
indexes and RF3 overhead. Callback p50/p99 was **13.409/796.807 ms**; enriched
creation-to-observation p50/p99 was **8,839.286/23,853.897 ms**, including buffering
and reader backlog. This mixed live/final-burst correctness run is not a capacity
benchmark. Lifecycle maintenance ran with writers stopped: retained readback
establishes persistence, not uninterrupted availability. The earlier crash tests
used live independent workloads.

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
| `orders-producer`, `enrichment-observer/`, `order_report.py` | Tracked repository links to the existing C++ witness producer and Rust/Python observation helpers; local enrichment-specific verification stays here |
| `prepare_workload.py`, `.build/enrichment/` | Materialize linked source as regular hard links/copies for local Docker builds; `.build/` is ignored and regenerated |
| `enrichment_lifecycle.py`, `enrichment_control.py`, `enrichment_probes.py`, `enrichment_metrics.py` | Example-owned lifecycle, fault controller, durable SQL probes and resource recorder |

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
