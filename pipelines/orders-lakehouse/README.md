# Kafka → Flink → Iceberg → Spark

A local, self-contained snapshot-publication and compaction recovery lab. Go
produces deterministic orders and fsynced Kafka ACK witnesses. Flink appends to
Iceberg over SeaweedFS; Spark reads pinned snapshots and rewrites small files.
Independent PyIceberg/Arrow checks every row, source offset and file partition.

## Quick start

1. `make docker-up` — build the pinned images and start this example's infrastructure.
2. `make up` — configure the catalog/topic and publish one million baseline orders.
3. `make test` — verify exact rows, Spark results and all three native data copies.
4. `make down` — reconcile, drain, inventory retained files, export evidence and stop services.
5. `make docker-down` — delete only this example's containers, network and volumes.

Requires Docker Compose v2 and Python 3.11+ for the standard-library controller;
application/Spark/Arrow toolchains run inside Dockerfiles. The all-profile budget
is **10.875 CPUs / 19.3125 GiB**, with another 2 GiB memory reserve. The tested
Docker VM had 11 CPUs / 24.42 GiB, ARM64 and Linux 6.12.76. Allow substantial free
space for the pinned engine images/builds as well as data. No ports are published.
The internal network, static credentials, explicit S3 region/endpoint and disabled
metadata lookup isolate this from real AWS configuration/accounts/resources.

## Operational commands

Between `make test` and `make down`, run these **serially**:

```sh
make publication-failure
make compaction-failure
make verify
```

`make test-production` invokes those three targets in that order. Each named
million-message phase runs once per fixture. Repeated `make up` verifies retained
ACKed state; it does not reseed. An existing witness or incomplete case prevents
blind producer replay. Export and finish the lifecycle before starting a fresh
fixture to repeat the whole sweep. `make report` curates the ignored raw results.

`ORDER_COUNT=1000000`, `CUSTOMER_COUNT=10000` and `INSERT_RATE=10000` are Make
variables. Keep them unchanged throughout a fixture. The producer uses paced
closed-loop 1,000-record batches, native same-session idempotence, `acks=all`,
ZSTD and one in-flight produce request per broker. Its 600-second outer deadline
is not a qualified stalled-request cancellation bound. Native retry/backoff
behavior comes from pinned franz-go 1.22.3; this is not a retry-policy matrix.

## Layout and ownership

```text
orders-lakehouse/
├── README.md, Makefile, compose.yaml
├── deploy/                # backend contract, one catalog config, Flink SQL, Seaweed settings
├── docker/                # pinned Flink, Spark and independent-oracle Dockerfiles
├── orders-workload/       # Go producer, Kafka/S3/catalog administration and ACK witness
├── lake-oracle/           # exact Arrow oracle, native Spark jobs and evidence export
├── scripts/               # lifecycle, separate faults, collectors and compact report
├── tests/                 # wrong-body, wrong-partition, duplicate and non-ACK controls
├── docs/RECOVERY.md        # recovery procedure, setup failures and remaining boundaries
├── reports/               # curated qualification; tracked
└── results/, .build/       # ignored histories, samples, datasets and generated SQL
```

There are no sibling Makefile/state dependencies or symlinks. All 30 configured
roles belong to Compose project `orders-lakehouse`; five are one-shot tools.

| Component | Tested version / role |
|---|---|
| Kafka | 4.3.2; three separate controllers and three brokers; three partitions, RF3, effective min-ISR 2, no unclean election |
| Flink | 2.3.0; one manager, two one-slot TaskManagers; five-second checkpoints into an owned shared local volume |
| Iceberg | Native 1.12.0, build `212aa894e919ff9fdf89160da4aadcbe6858ac01`; format 2, append-only, partitions `(phase, customer_shard)` |
| Catalog | Native JDBC catalog on PostgreSQL 19.0; fsync, synchronous commit and full-page writes verified on |
| Object store | SeaweedFS 4.55; three masters/volumes in simulated zones, replication `200`, two filers, etcd 3.7.4 ×3, one S3 gateway |
| Spark | 4.1.4, one master/two workers; native Iceberg Spark runtime; supported Java 17 / Python 3.12 runtime |
| Independent reader | PyIceberg 0.12.0 / Arrow 26.0.1; exact installed dependencies recorded in `deploy/oracle-requirements.txt` |

Spark 4.1.4 is the supported stable maintenance line for this Iceberg artifact;
Iceberg 1.12.0 does not publish a Spark 4.3 runtime. The Flink factory's pinned
source does not accept `catalog-type=jdbc`; use its supported explicit
`catalog-impl=org.apache.iceberg.jdbc.JdbcCatalog`. Hadoop client jars are included
because this Flink runtime still needs Hadoop configuration classes. Maven
artifacts use SHA-256 checks; external images use manifest digests.

## Measured outcomes — 2026-11-27 Pacific

One fixture, three one-million-message phases, 10,000 customers and one physical
Docker VM. Each phase contains unique IDs 1–1,000,000; the logical key is
`(phase, order_id)`. Each phase totals **599,500,000 cents**. Source partition is
`customer_id % 3`. All 11 output fields, partitions and acknowledged offsets are
checked, including duplicates, missing and unexpected records.

| Case | Observed result |
|---|---|
| Baseline | 1,000,000 exact rows in 60 files; independent Spark agrees; 180 native replica body reads / 13,509,819 bytes matched |
| Blocked publication + worker SIGKILL | Catalog UPDATE for checkpoint 48 blocked while publishing three files / 8,000 records. Same job restored checkpoint 48; three checkpoints failed and remained recorded. ACKs advanced 27,000 → 146,000 during the outage window; final 2,000,000 exact rows and old 1,000,000-row snapshot passed |
| Active compaction-worker SIGKILL | One native `ExecutorLostFailure`; Spark recomputed and replaced 119 files with 9 / 9,300,641 bytes rewritten. ACKs advanced 146,000 → 249,000; final 3,000,000 exact rows and old 2,000,000-row snapshot passed |
| Repeated setup | Verified all 3,000,000 retained rows without another producer run |
| Final data copies | 66 live files; 198 direct native replica body reads / 36,668,493 bytes matched |
| Retention inventory | 68 retained snapshots, 100 unique manifests, 185 conservatively referenced files, 193 listed Parquet objects. Eight unreferenced candidates / **503,220 bytes** remained; no expiry/deletion claim |

No missing, duplicate or wrong acknowledged logical keys were found in those
qualified snapshots. Managers, catalog and gateway remain single points of
failure. No physical disk loss or independent backup restore ran in this project.

### Timing and resource diagnostics

This is the approved **level 3** scope. The fixed 10,000-message/s source took
about 100 seconds per phase by design; it does not establish maximum capacity.
Producer timings cover a **1,000-record ProduceSync batch plus witness work**,
not single-record latency or end-to-end catalog visibility.

| Phase | Batch p50 / p99 / max, ms (1,000 batches) | Observed CPU core-s, at least | Sampled stack memory peak, GB | Recorded disk read / write bytes, at least |
|---|---|---:|---:|---:|
| Baseline | 6.443 / 102.559 / 1,942.907 | 106.333 | 6.65 | 1,806,336 / 147,738,624 |
| Publication recovery | 5.178 / 54.489 / 105.661 | 97.584 | 7.01 | 24,576 / 135,974,912 |
| Compaction recovery | 4.760 / 62.389 / 73.574 | 108.535 | 7.40 | 528,384 / 175,398,912 |

These phase totals include readback and background activity. They combine
available native persistent-role deltas and separately recorded one-shot actor
lifetimes. Restarted-role deltas, distroless etcd native counters, the SQL setup
client's final native counter, build CPU and host collector overhead are incomplete;
CPU samples still observe running roles. Do not interpret these lower bounds as
complete engine-only costs or rank baseline versus fault phases with different
cache/JIT states. Raw resource samples include CPU%, memory and network; one core
is 100%. Collection targets three seconds plus Docker collection time.

The pinned old-file reader made **74 / 77** successful observations during the
two recovery phases. Mean/max body-read times were **3.752 / 7.344 ms** and
**4.152 / 31.778 ms** respectively. Its checksum verifies a previously checked
Parquet body; it is not a complete latest-snapshot query. With fewer than 100
samples p99 is effectively maximum; p99.9 is unqualified. Full Spark verification
work took **8.778 s** baseline and **9.393 s** final, excluding session startup.
Native compaction work took **13.510 s**, including failure/recomputation.

Final live Parquet files occupy **12,222,831 bytes**. Footer column bytes were
84,150,406 uncompressed / 11,998,051 compressed: **7.014× ZSTD**. Baseline ratio
was 6.787×. This highly compressible deterministic corpus is not random payloads.
Codec CPU is included in role costs, not isolated; Kafka wire compression ratio
was not measured. Logical object bytes including retained state were **25,970,258**.
Named-volume inventory observed **806,592,512 allocated / 1,311,078,254 apparent
bytes**, including replicas, indexes, WAL, witnesses, checkpoints and events;
this is not unique physical-device accounting or a continuous disk-peak meter.
Native charged I/O omits cache hits and may miss later writeback. Physical I/O
service time/queue depth and controlled cold-cache queries are unavailable/unrun.
Old synthetic event timestamps do not make recently written files cold.

See [curated machine-readable outcomes](reports/QUALIFICATION.json) and
[recovery, retained failures and limitations](docs/RECOVERY.md). Initial setup
parser/endpoint mistakes and a PyIceberg adapter serialization error are preserved;
subsequent passes did not reload acknowledged data. All individual fault targets
ran serially; the convenience `test-production` wrapper was not replayed afterward.

## Upstream references

- [Iceberg releases and supported runtime matrix](https://iceberg.apache.org/releases/)
- [Iceberg Flink writes/checkpoints](https://iceberg.apache.org/docs/latest/flink-writes/)
- [Pinned Flink catalog factory](https://github.com/apache/iceberg/blob/apache-iceberg-1.12.0/flink/v2.3/flink/src/main/java/org/apache/iceberg/flink/FlinkCatalogFactory.java)
- [Pinned JDBC conditional metadata update](https://github.com/apache/iceberg/blob/apache-iceberg-1.12.0/core/src/main/java/org/apache/iceberg/jdbc/JdbcTableOperations.java)
- [Spark maintenance procedures](https://iceberg.apache.org/docs/latest/spark-procedures/)
- [Iceberg maintenance and orphan-retention warnings](https://iceberg.apache.org/docs/latest/maintenance/)
- [Pinned PyIceberg snapshot model/serializer](https://github.com/apache/iceberg-python/blob/pyiceberg-0.12.0/pyiceberg/table/snapshots.py)
- [SeaweedFS replication placement](https://github.com/seaweedfs/seaweedfs/wiki/Replication)
