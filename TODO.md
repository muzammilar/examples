# TODO

Candidate databases and systems to add as examples. Branch names follow the existing
`<system>-single-node`, `<system>-docker-compose-cluster` and `<system>-kubernetes-{operator,helm}` convention.

## Break and recover: all existing and future examples

**Current goal priority (2026-11-22):** the fresh DuckDB/ZFS full lifecycle,
ZFS repair-reader and Rust embedded follow-ups now pass and are documented below.
Continue with **Tigris, DuckDB and Iceberg-related projects** before other systems.
Investigate production operations, failure mechanisms and verified
recovery, with matched comparisons after correctness/recovery gates pass.

**Local-only scope:** all current tests use isolated Docker/Compose services and
lab-only credentials. Never use host AWS configuration/credentials or real cloud
resources. Tigris's managed service remains deferred; use SeaweedFS as the explicitly
named local S3 reference for DuckDB/Iceberg work, without attributing its results
to Tigris.

Apply the [production-test requirements](AGENTS.md#non-functional--production-tests) to every
system, including older examples. Deliberately find how it breaks, measure the
availability/data-loss boundary, and demonstrate recovery. Multiple replicas and
healthy pods alone do not qualify an example as production ready.

The [current pass and execution queue](PRODUCTION_TEST_AUDIT.md#current-testing-pass-and-next-work)
distinguish the 36 recent Makefile declarations, inspected coverage gaps and
actually executed evidence. The inventory refresh is not a passing-test report.

- [ ] Follow the [two- or three-pass testing approach](AGENTS.md#progressive-testing-across-systems):
  first survey every system in the agreed scope and record existing evidence/gaps;
  then establish runnable correctness, representative workloads, holistic measurements
  and initial recovery coverage across them; return in later rounds for deeper
  production/failure tests and repeated fair comparisons. Update README/TODO/audit
  after each pass; keep unrun cases and failed results explicit.
  Prioritize sustained performance comparisons once the evaluated configurations
  pass relevant correctness/recovery gates; failing systems need diagnosis first.
- [ ] Audit older examples for operational coverage; record missing, failed,
  blocked and unsupported cases without reclassifying them as passing.
- [ ] Audit and backfill holistic measurements for all experiments: CPU utilization
  and core-seconds, memory average/peak, measured compression and disk
  baseline/peak/settled usage, plus I/O/network and whole-stack costs. Preserve
  historical results and label missing evidence; throughput-only runs are incomplete.
- [ ] Apply the [evaluation stages and test concepts](AGENTS.md#evaluation-stages-and-backend-contract):
  declared backend/client contracts, concurrent lifecycle correctness, skewed and
  large-payload workloads, sustained saturation, replayable histories and explicit
  phase outcomes. Add applicable interrupted scaling, stale/empty member rejoin,
  compounded faults and routing/DNS/connection recovery tests. Report integrity,
  availability, automatic/manual recovery and resource cost separately.
- [ ] Add independent live writers/readers, an external durable witness,
  timestamped operation phases and numeric behavior reports where missing;
  observe read/write failures and latencies throughout each operation, then
  reconcile data and restored replicas.
- [ ] Add and run the applicable Compose and Kubernetes/operator tests: scaling
  both directions, rolling maintenance, leader/follower crashes, worker-node
  outage/restart, partition/lag, dependency failures, resource exhaustion and
  permanent disk/PVC replacement with native repair verification.
- [ ] Exercise operator outage/reconciliation, safe drain/PDB behavior and CR
  validation where applicable; keep workload and external ACK witnesses running.
- [ ] Go beyond a single tolerated failure where practical in isolated owned
  infrastructure: demonstrate quorum loss, correlated failures or exhausted
  capacity, then recover without hiding lost or corrupt data behind a reset.
- [ ] Verify backup/restore, fencing, data correctness and rebuilt redundancy;
  publish numeric RPO/recovery results and exact recovery commands, including
  gotchas, manual intervention and states that cannot be recovered.
- [ ] Run correctness/recovery before performance; retain raw evidence and
  document actual limits. Apply the same requirements to every future example.

## Current production-test backfill: last-month distributed examples

Scope this batch to deployments first added since **2026-10-16**; skip legacy
ClickHouse and other older examples. See [the source audit](PRODUCTION_TEST_AUDIT.md)
for the 33-directory cohort, evidence and concrete gaps. This narrower batch does
not remove the longer-term requirements above.

- [x] Rerun latest shared Neon workload on Kubernetes; five core plus five
  dependency/backup cases passed, 410,700 ACK rows reconciled without loss.
- [x] Isolate Greptime WAL initialization loss: failed bootstrap loses 100/100 ACK
  rows after restart; healthy control retains 100/100. Preserve the failed report.
- [x] Qualify additional Greptime operator drain/PDB, partitions, upgrade and pressure:
  all 15 distinct cases passed across the initial run/resumed targets; 627,200 ACK
  rows reconciled, four failed attempts preserved and node-IP recovery fixed.
- [x] Qualify the pgvector and LanceDB independent-reader/history backfills with
  native full-body/vector proofs, live process/reopen recovery, retained setup and
  archived evidence. pgvector also proves synchronous-standby crash, empty-disk
  clone and rebuilt-copy service. Extended cases remain in the audit; LanceDB's
  shared query workers are not independent database replicas.
- [x] Complete the exact Vespa single-node serial recovery aggregate and scoped
  cleanup; the flat aggregate and extended matrices remain in the audit.
- [x] Qualify Kafka Strimzi independent readers/preserved ACK histories, native
  process and directory recovery, repeated setup and full cleanup. The default
  serial aggregate checked 4,004,096 new ACKs; three workload trials checked
  6,004,096 ACKs (median 33,352.06 ACKs/s). The short-deadline disk run remains
  failed with 10,427 extra ambiguous physical copies and zero ACK loss; full
  binary evidence and exact failed-state fingerprints are preserved. Extended
  operator tests and isolated root-cause reproduction remain in the audit.
- [x] Replace MirrorMaker's numeric lag tolerance with native checkpoint decoding
  and Rust exact-offset/idle-group/ISR/EOF checks; fresh cutover checked 4,000,000
  unique orders. Live chaos and per-ID ACK-history work remains below.
- [x] Qualify MirrorMaker independent source/target readers and host-fsynced ACK
  histories: baseline, actual source-worker crash and retained-volume target-leader
  crash checked 6,003,072 acknowledged orders without reconciled loss/corruption.
- [x] Qualify fresh MirrorMaker empty-storage replacement and separate post-crash
  reconciliation: full replay checked 10,005,120 cumulative ACKs per cluster with
  zero loss/corruption; the interrupted experiment remains failed.
- [x] Finish MirrorMaker witness-backed teardown/recreation and image cleanup:
  774 + 280 archived files independently hash-checked, fresh 4,000,000-order
  cutover and retained readbacks passed; failed attempts remain recorded.
- [ ] Close independent-reader and preserved-history gaps in the recent relational,
  search, object-storage, messaging and archive examples identified by the audit.
- [ ] Extend applicable native repair, scaling, rolling changes, dependency/quorum
  loss and independent restore tests; publish only actually executed outcomes.

## Already covered

On `main`: Aerospike, Airflow, ArangoDB, CedarDB, ClickHouse, CockroachDB, Dragonfly, DuckDB, Elasticsearch, FoundationDB, Garnet/Valkey (Go comparison), InfluxDB, Kafka, kdb+, Manticore Search, Memgraph, Milvus, NATS, NebulaGraph, Neo4j, OceanBase, Postgres (k8s/vagrant), MySQL and SQLite clients, Qdrant, QuestDB, Redpanda, RisingWave, RonDB, ScyllaDB, SingleStore, Skytable, TiDB, Tarantool, TigerBeetle, TiKV, TimescaleDB, Valkey, Weaviate, YDB, YugabyteDB.

## Top priority

Fastest in class, free, runnable locally (see the sections below for the example names).

- [x] Tarantool
- [x] RisingWave
- [x] Redpanda
- [x] Manticore Search
- [x] Memgraph

## Priority

- [x] [Neon Kubernetes storage/recovery](postgres/neon/kubernetes-statefulset/): latest-workload million-row parent/branch checks, five core and five dependency/backup cases; 410,700 historical ACK rows retained and logical snapshot RPO quantified.

- [x] Qdrant [`turboquant/`](qdrant/turboquant/) (pinned v1.18.3): clustered float32 vs scalar int8 vs TurboQuant;
  exact recall@10, filtered/unfiltered p50/p99 and QPS, indexing and native
  RAM/page-cache/disk accounting, oversampling/rescoring tradeoffs, peer/disk
  recovery under live reads/writes. [Release and minimum version](https://qdrant.tech/blog/qdrant-1.18.x/).
- [ ] Postgres scale-out: Citus, PgDog, Multigres, Neon
- [ ] MySQL sharding: Vitess, ShardingSphere, PolarDB-X, NDB Cluster
- [x] pgvector
- [x] [StarRocks](starrocks/): single-node, Compose comparison and official operator; mutable-order correctness, benchmarks and 13 operator production cases, with failed attempts and recovery limits documented.
- [x] Iceberg + Trino + MinIO
- [ ] MongoDB replica set
- [ ] Temporal
- [ ] Restate
- [x] SeaweedFS — [replicated S3 and disk recovery](seaweedfs/s3-cluster/)

## Time-series

- [x] `timescaledb-single-node`: hypertables and continuous aggregates on Postgres.
- [x] `questdb-single-node`: ILP ingest and `SAMPLE BY` queries.
- [ ] `victoriametrics-docker-compose-cluster`: vminsert/vmselect/vmstorage as Prometheus long-term storage.
- [x] `influxdb3-core-single-node`: Arrow/Parquet engine with SQL.
- [x] [`greptimedb`](greptimedb/): single-node/Compose lifecycles and fresh operator recovery matrix passed; million samples + million logs per backend, ten operator cases and 207,000 ACK rows reconciled. Found and documented failed WAL initialization causing 3,400 lost samples; explicit repair, bootstrap guard and dual-network restart fix tested. Mimir/Cortex and tracing comparisons remain future work.
- [ ] `tdengine-docker-compose-cluster`: IoT time-series (AGPL); one table per device with super tables, vnodes sharding; rerun TSBS against TimescaleDB/QuestDB/InfluxDB.
- [ ] `iotdb-docker-compose-cluster`: Apache IoTDB (Apache 2.0) ConfigNode/DataNode cluster, TsFile columnar storage; same TSBS run.
- [ ] `dolphindb-single-node`: free Community Edition (memory/core caps); columnar time-series engine with its own scripting language, used for tick data; compare with kdb+ on the same market data.
- [ ] `arcticdb-local`: Man Group's DataFrame time-series DB (C++ engine, Python API) on local LMDB and MinIO/S3 backends; versioned symbols, append/update.

## Streaming databases and CDC

- [x] `risingwave-single-node` (top priority): Postgres-compatible incremental materialized views with built-in CDC.
- [ ] `materialize-emulator`: strict-serializable incremental views.
- [ ] `feldera-single-node`: incremental SQL computation (DBSP).
- [ ] `timeplus-proton-single-node`: C++ streaming SQL on ClickHouse code (Apache 2.0); streaming joins and windows from Kafka/Redpanda; compare with RisingWave.
- [ ] `debezium-postgres-kafka`: CDC from Postgres into Kafka, then ClickHouse.
- [ ] `debezium-mysql-kafka-flink-clickhouse`: end-to-end e-commerce CDC pipeline, MySQL → Debezium → Kafka → Flink → ClickHouse, orchestrated by Airflow.
  - **Source:** MySQL 8 with `binlog_format=ROW`, `binlog_row_image=FULL`, GTIDs on; `demo` schema with `products`, `orders` (`pending`/`completed`/`canceled`), `order_items` (price and cost price for margin); `debezium` user with `REPLICATION SLAVE`/`REPLICATION CLIENT`/`RELOAD`/`SHOW DATABASES`; seed data plus a load generator for inserts/updates/deletes.
  - **Capture:** Debezium MySQL connector (`io.debezium.connector.mysql.MySqlConnector`) with an initial snapshot and then binlog streaming; `table.include.list`, schema-history topic, `ExtractNewRecordState` unwrap SMT, Avro via Schema Registry (Confluent or Apicurio).
  - **Kafka, two variants:** Docker Compose (KRaft Kafka + Kafka Connect + Schema Registry, connector registered by `curl` to `:8083/connectors`) and Kubernetes with Strimzi (`Kafka` with KRaft node pools, `KafkaConnect` with a Debezium plugin image built via `spec.build`, `KafkaConnector` CR for the MySQL source, `KafkaTopic` CRs for per-table topics).
  - **Processing:** Flink SQL with the Kafka connector and `debezium-avro-confluent` format; joins `order_items` with `products` and `orders` into per-order revenue/margin, tumbling-window aggregates, exactly-once via checkpoints.
  - **Sink:** ClickHouse, either through the Flink ClickHouse connector or the ClickHouse Kafka Connect sink (Strimzi `KafkaConnector`); `ReplacingMergeTree(version)` keyed on primary key with `is_deleted` for Debezium deletes, plus a `SummingMergeTree` materialized view for daily revenue. Compare with ClickHouse's native `Kafka` table engine + MV as the no-Flink baseline.
  - **Airflow:** DAGs that apply MySQL migrations (`migration_history` table), register/pause/restart connectors through the Connect REST API (or apply Strimzi CRs), trigger Debezium incremental snapshots via a signal table for backfills, and run nightly row-count/sum reconciliation between MySQL and ClickHouse.
  - **Ops:** Kafka UI (Conduktor, Redpanda Console or AKHQ) to browse topics and schemas; show a schema change (add column) flowing through the schema-history topic and Schema Registry compatibility rules; failure demos: restart Connect mid-stream, drop and recreate a Flink job from a savepoint.

## Message streaming and queues

- [x] `redpanda-single-node` / `redpanda-docker-compose-cluster` (top priority): Kafka API without JVM or ZooKeeper; compare with the Kafka examples.
- [ ] `iggy-single-node`: Apache Iggy, Rust message streaming over TCP/QUIC/HTTP.
- [ ] `fluvio-single-node`: Rust streaming with WASM SmartModules.
- [ ] `pulsar-single-node`: broker/BookKeeper split, tiered storage.
- [ ] `rabbitmq-streams`: classic queues vs streams.
- [ ] `aeron-cluster`: Java/C++ microsecond messaging over UDP/IPC with Raft-replicated Aeron Cluster.
- [ ] `kurrentdb-docker-compose-cluster`: KurrentDB (formerly EventStoreDB) event store; append-only streams, optimistic concurrency, projections, persistent subscriptions; 3-node cluster with `failover`.

## Durable execution and workflow engines

- [ ] `temporal-docker-compose-cluster`: workflows, activities, retries, signals; Go and Python workers backed by Postgres.
- [ ] `restate-single-node`: Rust durable execution runtime; virtual objects, durable promises, idempotent handlers.
- [ ] `inngest-self-hosted`: self-hosted Inngest server with Postgres + Redis; steps, retries, throttling, fan-out (complements `js/inngest-js` and `python/inngest-py`).
- [ ] `hatchet-single-node`: Postgres-backed task queue and DAG workflows.
- [ ] `dbos-postgres`: durable workflows as a library on top of Postgres.
- [ ] Comparison example: same order/payment saga implemented in Temporal, Restate and Inngest.

## Postgres and MySQL

No dedicated `postgres/` or `mysql/` folder yet; existing examples live with other systems.
Planned: Postgres, Citus, PgDog, Multigres and Neon under `postgres/`; MySQL, Vitess and friends under `mysql/`
(see the two sections below).

| Example | Engine | What |
|---|---|---|
| [`k8s/hello-postgres/`](k8s/hello-postgres) | Postgres | PostgreSQL-HA via Helm |
| [`vagrant/multi-vm-postgres/`](vagrant/multi-vm-postgres) | Postgres | Postgres across Vagrant VMs |
| [`go/sqlc-students/`](go/sqlc-students) | Postgres | Go + sqlc |
| [`timescaledb/`](timescaledb) | Postgres | Time-series extension |
| [`python/mysql-client/`](python/mysql-client) | MySQL | Python client |
| [`clickhouse/named-collection-mysql/`](clickhouse/named-collection-mysql) | MySQL | ClickHouse reading MySQL |

## Postgres ecosystem

- [x] `postgres/customer-sharding`: PgDog + Patroni, three shards with three copies each; primary failures, replica stalls and acknowledgement reconciliation.

Planned as a `postgres/` folder: a plain Postgres baseline, then each scale-out option on the same
sharded `orders` workload (distribution key `customer_id`) so results compare directly.

- [ ] `postgres-single-node`: Postgres 18 baseline; async I/O, `pg_stat_statements`, `pgbench`.
- [ ] `postgres-docker-compose-cluster`: streaming replication with Patroni + etcd; `failover`, sync vs async commit.
- [ ] `postgres-kubernetes-operator`: CloudNativePG on kind; replicas, switchover, backups to MinIO.
- [x] `citus-docker-compose-cluster`: 3 replicated worker groups plus replicated coordinator; colocated distributed tables, worker/coordinator failover, replica stalls and active-write rebalancing 3 → 5 → 3.
- [x] `pgdog-docker-compose-cluster`: separate pooling configuration example, 3 PostgreSQL copies, pool-size reload/comparison, replica routing and primary failover. Customer sharding is a separate example; online resharding remains future work.
- [ ] `multigres-docker-compose-cluster`: Supabase's Vitess-style architecture for Postgres (gateway, pooler, orchestrator); early-stage, document what runs and what doesn't.
- [ ] `pgbouncer-pooler`: transaction pooling baseline; compare connection-storm behaviour with PgDog.
- [ ] `readyset-cache`: ReadySet (BSL) wire-compatible query cache for Postgres and MySQL; incrementally maintained cached queries fed by replication; same read-heavy `orders` queries with and without it.
- [ ] Comparison example: same `orders` workload and `pgbench` script on single Postgres vs Neon vs Citus vs PgDog vs Multigres; single-shard vs cross-shard latency, resharding under load. Neon scales storage and read replicas, not writes: it shows the cost of remote storage (cold-cache reads, commit latency through safekeepers) against local-disk Postgres.
- [x] [`postgres/pgvector-product-search`](postgres/pgvector-product-search/): single and three-copy Patroni profiles, 100,000-product inventory joins, filtered HNSW/hybrid retrieval, full-row replica checks, primary loss and measured benchmarks. Milvus/Qdrant/Weaviate comparisons remain separate work.
- [ ] `paradedb-single-node`: BM25 full-text search inside Postgres.
- [x] [Neon single-node and clustered recovery](postgres/neon/): final source-materialization lifecycles, benchmarks and five independent-reader recovery cases passed; 133,600 ACK rows retained, all million-row PostgreSQL copies and post-recovery branch checked. Kubernetes qualification is documented separately; clustered object storage is Garage.
- [ ] `orioledb-single-node`: undo-log storage engine for Postgres.
- [ ] `cloudberry-docker-compose-cluster`: Apache Cloudberry (open-source Greenplum successor) MPP Postgres; coordinator + segments, distributed analytic queries; compare with Citus for analytics.
- [ ] `opengauss-single-node`: Huawei's Postgres-derived OLTP engine (Mulan PSL v2); MOT in-memory engine and NUMA-aware threads vs plain Postgres on `pgbench`.
- [ ] `pg-duckdb-mooncake`: columnar analytics inside Postgres with `pg_duckdb` and `pg_mooncake`; same queries as the DuckDB and ClickHouse examples.
- [ ] `alloydb-omni-single-node`: Google AlloyDB Omni (free developer use, closed source) in Docker; columnar engine and adaptive autovacuum vs plain Postgres on the same OLTP + analytic queries.

## MySQL ecosystem

- [x] `mysql/customer-sharding`: separate Vitess workload with 3 customer shards, 3 copies per shard, live primary failures on every shard, GTID catch-up and acknowledged-data tracking.

Planned as a `mysql/` folder, mirroring the Postgres section with the same `orders` workload.

- [ ] `mysql-single-node`: MySQL 8.4 LTS baseline; `sysbench` OLTP.
- [ ] `mysql-docker-compose-cluster`: InnoDB Cluster (Group Replication) + MySQL Router; single-primary `failover`.
- [ ] `mysql-kubernetes-operator`: Oracle MySQL Operator or Percona Operator for MySQL on kind.
- [ ] `mariadb-galera-cluster`: multi-primary synchronous replication; write conflicts and certification failures.
- [ ] `proxysql-read-write-split`: query routing, read/write split and query rules in front of replicas.
- [x] `vitess-docker-compose-cluster`: unsharded deployment, 3 MySQL copies, VTGate/VTctld/etcd/VTOrc, semi-sync primary failure and million-order loss tracking.
- [ ] Extend Vitess deployment with online `Reshard` workflow and scaling measurements.
- [ ] `vitess-kubernetes-operator`: PlanetScale `vitess-operator` on kind.
- [x] `ndb-cluster-docker-compose-cluster`: Community 8.4.11; 3 partitions × 3 copies, data/SQL/management failures, loss tracking and Vitess architecture comparison. Separate correlated disk-full crash lost 6,800 acknowledged orders.
- [ ] `shardingsphere-proxy`: Apache ShardingSphere-Proxy in front of plain MySQL (and Postgres) shards; sharding rules, read/write split, distributed transactions.
- [ ] `polardbx-kubernetes-operator`: PolarDB-X (Apache 2.0) on kind; CN/DN/GMS/CDC split, partitioned tables, global secondary indexes.
- [ ] `matrixone-docker-compose-cluster`: MySQL-compatible HTAP (Apache 2.0, Go); separated storage/compute on S3 (MinIO), CN/TN/LogService; OLTP plus TPC-H on one copy of the data.
- [ ] Comparison example: same `orders` workload on single MySQL vs Vitess vs ShardingSphere vs PolarDB-X vs NDB/RonDB vs TiDB (existing example); then Vitess vs Citus/PgDog/Multigres for the MySQL-vs-Postgres sharding story.

## In-memory OLTP

- [ ] `volt-active-data`: partitioned single-threaded in-memory SQL with stored procedures (H-Store lineage); Developer Edition is a free 100-day license, arm64 unknown.
- [ ] `spacetimedb-single-node`: transactional Rust modules and client subscriptions for multiplayer world state; reconnect/replay and crash recovery versus PostgreSQL + an application/WebSocket service. Qualify current license and native replication support; deferred until the current batch finishes.

## Real-time OLAP

- [ ] `starrocks`: single-node, replicated Compose cluster, Kubernetes operator and clustered mutable orders/inventory joins. Compare ClickHouse; add shared-data/S3 compute-loss and cache experiments where supported.
- [ ] `doris-docker-compose-cluster`: mutable orders/inventory joins, replicated tablets versus storage/compute separation, compute isolation and disk rebuild; compare StarRocks / ClickHouse on identical data.
- [ ] `pinot-docker-compose-cluster`: Kafka delivery-status upserts, duplicate/out-of-order events, segment recovery, star-tree-compatible aggregates and server loss; compare StarRocks / ClickHouse with equivalent visibility rules.
- [ ] `druid-docker-compose-cluster`: segments, ingestion specs.
- [ ] `databend-single-node`: Rust cloud warehouse on object storage.
- [ ] `firebolt-core-single-node`: free self-hosted Firebolt engine; compare with ClickHouse and StarRocks.
- [ ] `exasol-personal`: free single-user in-memory MPP; Docker image is x86-only and capped at 10 GiB.
- [ ] `vertica-community`: free Community Edition (3 nodes, 1 TB); MPP column store with projections; compare with ClickHouse and StarRocks.
- [ ] `cratedb-docker-compose-cluster`: industrial telemetry plus text filters, tenant skew, three shards with three copies, relocation and disk loss; compare GreptimeDB / TimescaleDB / Elasticsearch on matching query subsets.
- [ ] `heavydb-single-node`: HeavyDB (formerly OmniSciDB/MapD, Apache 2.0) GPU-accelerated SQL; CPU mode on a Mac, GPU numbers only with an NVIDIA box.

## Embedded analytics

- [x] `cedardb/backup-recovery`: synchronous million-row fixtures versus PostgreSQL, live independent actors, graceful/SIGKILL recovery, online physical backups, read-only fresh-copy restores, full ACK/body/history reconciliation and CPU/memory/compression/I/O/storage observations. CE privileges/async-commit and PostgreSQL replication-HBA gotchas documented.
- [x] `cedardb/backup-recovery` analytical contention: [12 sustained cells](cedardb/backup-recovery/HTAP.md), three fresh-process trials per backend/traffic condition, 60 s warmup and 300 s monotonic measurement deadlines. Complete bodies and 109,580 historical ACKs verified, zero missing. Median CedarDB query throughput was 3.33× PostgreSQL alone and 2.69× with live appends/readers; PostgreSQL used less charged memory. Resource costs and the documented 2.91 ms UTC timing discrepancy remain in the report. This immutable subset plus live appends does not cover UPDATE/DELETE contention or cold-cache behavior.
- [x] CedarDB/PostgreSQL [mutable workload foundation](cedardb/backup-recovery/MUTABLE.md): Go paired UPDATE and DELETE/reinsert, transactional receipts, independent snapshot readers, exact body/version/aggregate oracle and native read-only/rollback controls. Four million-row healthy/SIGKILL cases verify **2,972 ACKed transactions**, zero acknowledged loss or wrong/partial rows; 23 controller tests and fresh million-row driver controls pass. Earlier 10,000-row cases and short diagnostics remain separate. CPU/memory/compression/disk/byte-I/O and native IOPS recorded; preserve the PostgreSQL CASE failure and invalidated prototype, archived before fixture replacement. The [12-cell sustained million-row comparison](cedardb/backup-recovery/MUTABLE_BENCHMARK.md) passes: 108,440 ACKs and 83,243 complete query results, zero loss/corruption; CedarDB median query throughput 3.29×/3.03× alone/live, PostgreSQL lower memory. Automatic-statistics preparation and warm-query scope remain explicit.
- [ ] CedarDB follow-up: add explicitly prepared planner statistics and captured query plans rather than inferring readiness in every case from automatic analysis; repeated single/batch/bulk and cache-temperature comparisons; bounded disk-full/pressure (including the observed `db.ballast`/WAL footprint), interrupted backup and supported upgrade paths. A single-pod kind StatefulSet may test deployment/PVC behavior; native CedarDB clustering/HA remains unsupported by this example.

- [x] `rayforce/duckdb-comparison`: analytical queries plus single/batch query and typed append paths through Python/C++/Rust; 144 ingestion trials, full old/new body verification, CPU/memory and disk accounting. In-memory, single-writer scope.
- [x] Extend that comparison with DuckDB columnar data-chunk Appender: 72 correctness cells and 144 fresh-process performance trials, per-batch query-visible acknowledgements, identical inputs and full-body verification.
- [x] Rayforce/DuckDB saved snapshots: matching two-million-trade/10,000-customer fresh-process reopens, exact full-body/group/join checks, native file/encoded-column/codec evidence, sampled disk growth and CPU/memory/I/O costs. One functional snapshot observation per engine; no capacity or live recovery claim.
- [x] Rayforce synchronous native IPC journal recovery: two live-writer/reader SIGKILL cycles, second snapshot plus tail, external fsynced witness, exact two-million-trade/10,000-customer and all-ACK reconciliation, CPU/memory/disk/I/O/compression evidence. Single-process correctness, not capacity or HA.
- [x] Rayforce fenced snapshot/tail restore into separate owned local files with the original input unavailable to the database: zero replay errors, full seed/customer/ACK proofs and measured CPU/memory/disk/I/O/compression. Same-host offline restore, not live backup or independent-host durability.
- [x] Rayforce cross-container journal writer exclusion under live traffic, plus two-crash recovery on owned Linux volumes. Ten second-open attempts rejected, exact full-body/ACK reconciliation, native block I/O measured. Preserve/document the failed host-share ownership case and standard-library filesystem controls; do not generalize its unsupported locks to all bind mounts.
- [x] Rayforce controlled torn-tail diagnostic: refuse startup, validate the intact prefix, repair only the exact known three-byte trailer in a fresh copy, and restore all seed/customer/ACK bodies. Preserve original/damaged files; the six-case recovery profile creates its own source. Offline format recovery, not real device failure or general corruption repair.
- [x] Rayforce [interior payload-corruption evaluation](rayforce/duckdb-comparison/CORRUPTION.md): invalid types fail closed, but a valid one-bit price edit replays **one wrong acknowledged body — FAILED integrity**. Untouched backup restores all 2,000,000 seed trades, 10,000 customers and 2,269 ACKs correctly. The full production target propagates the failure; logical file-edit fault model, not physical bit rot or normal crash loss. Resource/storage costs and failed state retained.
- [x] Rayforce [middle-header corruption](rayforce/duckdb-comparison/HEADER_CORRUPTION.md): native startup rejects one-bit magic/version damage, but applying its prefix-truncation hint to another copy loses **566 ACKs** with clean startup. Guard rejects both; untouched backup recovers all 2,000,000 seed/10,000 customer/2,259 ACK bodies. Preserve unsafe recovery as **FAILED durability** under the logical file-edit fault model, with CPU/memory/compression/disk/I/O costs.
- [x] Rayforce [native AOF API diagnostic](rayforce/duckdb-comparison/AOF.md): C++ commit/scan application, 10,000 × 1,024-byte opaque events, independent full-body/witness checks and all CPU/memory/compression/disk/I/O costs. Payload edits reject; length/commit-marker edits silently omit **5,000 / 5,000 / 100 ACKs — FAILED integrity** under the offline file-edit model. Known uncommitted trailer and intact-backup verification pass. Buffered append and commit timings have separate scopes; no ordinary crash or automatic table-persistence claim.
- [x] [DuckDB native live WAL recovery](rayforce/duckdb-comparison/DUCKDB_RECOVERY.md): signed Quack C++ TLS sessions, two retained-volume SIGKILLs, live readers/writers, checkpoint under load, full two-million-trade/10,000-customer and 2,364-ACK reconciliation with zero loss/corruption. CPU/memory/compression/disk/block-I/O and recent-versus-historical read timings recorded. Full serial production suite preserves both known Rayforce integrity failures; final five-command lifecycle passes. Functional diagnostic, not HA/capacity or a recovery-performance ranking.
- [x] Rayforce [AOF segment recovery boundaries](rayforce/duckdb-comparison/AOF_SEGMENTS.md): 159 native segments, complete 10,000/10,003-body rotation/reopen/backup checks, exact known three-byte tail handling and full-scan rejection of sealed CRC damage. Native open alone misses sealed corruption; deleting the tail loses 46/49 ACK bodies and the external witness rejects. CPU/memory/compression/disk/I/O and per-operation timings recorded. Expected-loss diagnostic, not a pass for existing strict integrity failures, physical disk loss or live availability.
- [ ] Rayforce/DuckDB: repeated alternating recovery-performance trials, asynchronous journal/watermark recovery, interrupted snapshot replacement, compressed journal frames, AOF live concurrent scan/process-crash and interrupted rotation, missing first/interior segments, online backup/restore, pressure, prepared SQL and genuinely cold persisted-query matrices.

- [x] [`duckdb-zfs-recovery`](duckdb/zfs-recovery/FULL_SUITE_RESULTS.json): fresh five-command lifecycle passes; continuous guest-crash recovery verifies 2,000,000 seed + 323,000 acknowledged rows and 180 independent reads with zero missing/wrong/partial bodies. The full disk/crash/replica/archive sequence verifies 7,453,000 exported bodies across copies and preserves all 30,000 source acknowledgements absent from the asynchronous standby. Effective 256 MiB ARC persists across reboot; all owned resources are removed. CPU/memory/compression/disk/I/O evidence and earlier failures remain explicit. Quack overrides the requested HTTP timeout, so no three-second RPC deadline, capacity comparison or complete production qualification is claimed.
- [ ] DuckDB/ZFS deeper pass: isolate Quack connection/query/fetch/close timings and qualify a supervised deadline with ambiguous-write reconciliation; add further dependency/pressure/restore and retention cases. Keep physical host-loss durability and repeated fair recovery-performance comparisons unqualified.
- [x] [ZFS storage recovery follow-up](zfs/storage-lab/): default 4 MiB/s five-command lifecycle, full mirror/RAIDZ2/replication suite, 157/140 complete immutable-body reads during recovery, verified 512 MiB payloads, clean scrubs, guarded native pool archives and scoped teardown. Thirty-five controls pass; CPU/memory/compression/disk-byte/IOPS observations published. Earlier explicit-rate checkpoint and archive-checker/Python-prerequisite failures remain separate; stream validation is not restore qualification.
- [ ] ZFS deeper pass: restore the preserved native streams, add continuous replication observation and per-flush external acknowledgements, and exercise guest restart/configuration persistence, bounded pressure and further loss combinations. Current Tigris/DuckDB/Iceberg priorities come first.

- [ ] `hyper-api-embedded`: Salesforce/Tableau Hyper (Umbra lineage, compiled queries) via `pip install tableauhyperapi`; free, closed source. macOS arm64 wheels stop after 0.0.26479 (newer releases: Linux x86_64 and Windows only), so pin that on a Mac. Compare with DuckDB on the same data.
- [ ] `polars-datafusion`: Polars and DataFusion on the same Parquet files as the DuckDB example.
- [ ] `monetdb-single-node`: MonetDB (MPL 2.0), the original column store (vectorized execution lineage of DuckDB); same queries as the DuckDB example.

## Lakehouse and object storage

- [ ] [`tigris/`](tigris/): resilient S3 application example and comparison with
  Garage, SeaweedFS, RustFS and Ceph RGW; optional managed baselines S3/R2.
  Use identical bodies, checksums, request mixes and client budgets; measure
  single/multipart upload, range reads, conditional writes, visibility and
  interrupted-upload recovery under independent live writers/readers. Account
  and isolated bucket required for a separately requested managed run; currently
  deferred under the local-only scope. Distinguish managed-service observations from
  local disk/node repair proofs; verify deployment availability before promising
  self-hosted Tigris or provider-internal fault injection. Planned, not run.

- [ ] `hoglake-local`: [PostHog Hoglake](https://github.com/PostHog/hoglake), a Postgres-native lakehouse catalog; investigate its native Trino connector and catalog recovery. Documentation backlog only; no implementation in the current Iceberg + Trino + MinIO work.
- [x] `iceberg-trino-minio`: persistent Iceberg REST fixture + 4 MinIO nodes + Trino 484 coordinator/3 workers; million-row reconciliation, worker task retry, storage-node failure and catalog restart.
- [x] `iceberg/multi-system`: five SeaweedFS-backed DuckDB/ClickHouse data paths, Spark compaction, safe archive handoffs and a three-shard/three-copy native serving cache with replica recovery.
- [x] [Iceberg live storage-crash qualification](iceberg/multi-system/LIVE_RECOVERY_RESULTS.json): two live writers and two independent readers, 2,000,000 seed rows / 10,000 customers; corrected crash case verifies 2,490,000 acknowledged rows per engine and freshly rechecks the earlier 2,480,000-row table. Snapshot-bound compression, CPU/memory/disk/I/O, latency and original instrumentation failures are recorded. Guarded teardown and offline closed-history checks pass; unrelated containers are unchanged. This is one-host retained-volume mini recovery, not HA, disk loss or capacity qualification.
- [x] [Iceberg short-lived tool resource reporting](iceberg/multi-system/RESOURCE_REPORT_RESULTS.json): all five functional paths produce 22 native tool reports plus Docker lifetime events; Spark observes Python/JVM separately. Corrected coarse-tick CPU aggregation replays original samples; its rebuilt image and 15 native controls pass. Cleanup preserves unrelated containers. This is resource-accounting qualification, not a complete capacity benchmark.
- [ ] Current stable DuckDB 2.0.x: qualify the latest stable core (2.0.1 at the 2026-11-22 release check), matching extension builds and actual engine versions alongside the historical 1.5.5 lane. Run the same Iceberg interoperability, manifest-I/O failure, compaction/recovery and Quack deadline checks. Verify file-format compatibility on owned copies; do not mutate historical native databases or transfer 1.5.5 limitations to 2.0 without reproducing them. Check whether the Go binding bundles 2.0.1; its `v2.20000.0` release bundles 2.0.0.
- [ ] Iceberg compactor-only crash qualification: the fresh two-million-row case passed live baseline compaction and completed-output validation, but storage hit its 1 GiB OOM limit after Spark SIGKILL and DuckDB's reader aborted on a manifest I/O exception. Same-volume restart plus diagnostic dual-engine reconciliation preserved all 3,390,000 ACK rows; all 36 ambiguous batches were absent. Original qualification stays failed. Investigate backend memory and the native exception boundary, then complete isolated compactor recovery; keep the failed case and bounded-spill fix documented in `iceberg/multi-system/PLAN.md`.
- [ ] Iceberg deeper pass: measure normal-path snapshot compression and full startup/exit costs; isolate catalog/S3/network outages, compactor interruptions and archive/TTL recovery; then run comparable sustained cold/warm performance trials. Managed Tigris remains deferred; use isolated local SeaweedFS without host AWS configuration or resources.
- [x] `clickhouse/iceberg`: single-server catalog writes, retained snapshots, verified export before TTL deletion, compaction and recovery checks.
- [ ] Extend the Iceberg REST catalog coverage to Polaris/Nessie; DuckDB and ClickHouse readers are covered with SeaweedFS.
- [ ] `ducklake-multi-writer`: independent DuckDB writers coordinated through a PostgreSQL DuckLake catalog; deferred at the user's request.
- [ ] `delta-lake-duckdb`: Delta tables read from DuckDB and Spark.
- [ ] `paimon-flink-minio`: Apache Paimon (LSM-based primary-key tables, changelog for streaming) written by Flink to MinIO; read from Spark/StarRocks/Trino; compare upsert throughput and freshness with Iceberg.
- [x] `garage/s3-cluster`: nine nodes, three zones, fsync/quorums, empty-disk replacement and physical replica audits.
- [x] `seaweedfs/s3-cluster`: separated HA master/volume/filer/etcd/S3 roles, three-datacenter `200` replication, fsync, disk replacement and full replica-byte checks.
- [ ] SeaweedFS extensions: erasure coding for warm volumes and `filer.sync` cross-cluster replication.
- [x] `rustfs/s3-cluster`: four-node EC2+2, strict durability, deep heal and independent rebuilt-shard checks.
- [x] [`ceph/s3-cluster`](ceph/s3-cluster/): three guests, nine BlueStore OSDs, three RGWs, host-domain replication, native deep scrubs, guest crash and permanent disk replacement under load, measured emulated benchmarks and operational gotchas.
- [x] `clickhouse/s3-tiered-storage`: replicated MergeTree TTL movement to Garage, hot/cold/cache benchmarks and three separate failure tests.
- [ ] Comparison example: 1-4 MB blob put/update/delete latency in SeaweedFS vs Aerospike (`write-block-size 8M`) vs ScyllaDB (large-cell threshold, 16 MB mutation cap); plus metadata-in-Scylla + blob-in-SeaweedFS pattern.

## Low-latency and HPC storage

- [x] `zfs-storage-lab`: dedicated mirror, RAIDZ2 and resumable snapshot-replication setups with fio verification and bounded benchmarks.

- [ ] `daos-single-node`: DAOS (`daos-stack/daos`) over `ofi+tcp` in MD-on-SSD mode (no RDMA/PMem); `daos_server` + `daos_engine` + `daos_agent`, `dmg` pool setup, containers, `pydaos` KV API, `dfuse` POSIX mount, replication vs EC object classes.
- [ ] `tidehunter-embedded`: Mysten Labs' WAL-as-storage KV engine (Rust, used by Sui validators); compare write amplification and point reads with RocksDB.
- [ ] `cockroachdb-value-separation`: Pebble blob separation (v25.4+) on vs off for large values.
- [ ] `hazelcast-docker-compose-cluster`: Java in-memory data grid; IMap, near cache, CP subsystem; latency vs Valkey/Dragonfly.
- [ ] `chronicle-map-embedded`: Java off-heap persisted map, microsecond latency; compare with LMDB.

## Document

- [ ] `mongodb-replicaset`: replica set and change streams.
- [ ] `ferretdb-single-node`: MongoDB wire protocol on Postgres (DocumentDB extension).
- [ ] `couchbase-single-node`: KV + N1QL + indexes.

## Search

- [ ] `opensearch-docker-compose-cluster`
- [ ] `meilisearch-single-node`: Rust, typo-tolerant app search.
- [x] [`typesense/product-search`](typesense/product-search/): consolidated single/three-Raft-replica storefront workload and three application-managed customer shards with three replicas each, 100,000 products, typo/facet/filter tests, hybrid ranking, leader-loss reconciliation and measured benchmarks.
- [ ] `quickwit/`: Rust log search on object storage; supported single/Compose/Kubernetes deployments plus a clustered Kafka-to-S3 immutable-log workload, split/index recovery and indexer/searcher loss. Compare matching log queries with Loki / OpenObserve / GreptimeDB; qualify release health and current licensing.
- [x] [`vespa/phased-product-search`](vespa/phased-product-search/): single/three-content-node profiles, 100,000 products, four ranking modes, controller/node failure under load, native metadata/version verification on every physical copy and measured benchmarks.
- [x] [Vespa storage sharding](vespa/phased-product-search/#native-storage-sharding): 3 groups × 3 content nodes plus separate control/query hosts, 900,000 replica-directed gets per phase, content-node failure with 0 acknowledged loss and four-mode benchmarks.
- [x] `manticore-single-node` (top priority): columnar storage + secondary indexes; rerun db-benchmarks.com against the Elasticsearch example.

## Key-value and cache

- [ ] `redis8-single-node`: Redis 8 (AGPL option) with built-in JSON, search and vector sets.
- [ ] `kvrocks-single-node`: Redis protocol on RocksDB.
- [ ] `garnet-docker-compose-cluster`: Microsoft Garnet (.NET) standalone and cluster mode; Tsavorite storage with tiering to disk, checkpoints and AOF.
- [ ] `keydb-active-replica`: multithreaded Redis fork (Snap) with active-active replicas; low priority, last release v6.3.4 (Oct 2023).
- [ ] `pika-single-node`: Redis protocol on RocksDB (OpenAtom); compare with Kvrocks for larger-than-memory data.
- [ ] `ocache-docker-compose-cluster`: Tigris Data's Go cache (in-memory + RocksDB) single node and 3-node cluster; gRPC/HTTP APIs, versioned CAS ops, `ocachecli bench`.
- [x] `tarantool-single-node` / `tarantool-replicaset` (top priority): in-memory DB with WAL and Lua stored procedures; memtx vs vinyl engines, synchronous replication.
- [ ] `memcached-extstore`: pure-cache latency baseline; extstore for large values on flash.
- [ ] Comparison example: same workload (GET/SET, pipelines, large values, persistence on) across Valkey, Redis 8, Dragonfly, Garnet, KeyDB, Kvrocks, Pika, Tarantool, ocache and Memcached.
- [ ] `etcd-docker-compose-cluster`: Raft, watches, leases.
- [ ] `ignite2-docker-compose-cluster`: partitioned inventory reservations, colocated computation, transactions, persistent restart and rebalance under load; compare Hazelcast / PostgreSQL / Tarantool. Include single-node and supported Kubernetes deployment.
- [ ] `ignite3-docker-compose-cluster`: distribution zones, colocated transactions, majority loss and replica rebuild; compare Ignite 2. Research lab only: official October 2026 status has no next Ignite 3 release planned and recommends Ignite 2.x for new production deployments.
- [ ] `infinispan-docker-compose-cluster`: Red Hat Infinispan (Apache 2.0) data grid; distributed caches, RESP endpoint, cross-site replication; same grid comparison.
- [ ] `oxia-docker-compose-cluster`: StreamNative Oxia (Apache 2.0) sharded metadata store, ZooKeeper/etcd alternative; compare watches, sessions and write throughput with etcd.

## Graph and multi-model

- [x] `memgraph-single-node` (top priority): in-memory Cypher; compare with Neo4j.
- [ ] `ladybugdb-embedded`: embedded columnar graph DB, successor to the archived Kùzu.
- [ ] `arcadedb-single-node`: Apache 2.0 multi-model.
- [ ] `surrealdb-single-node`: SurrealDB 3.0 multi-model (document, graph, vector, time-series).
- [ ] `dgraph-single-node`
- [ ] `falkordb-single-node`: graph on sparse matrices (GraphBLAS) as a Redis-protocol module, OpenCypher; compare with Memgraph and Neo4j on the fraud workload.
- [ ] `tigergraph-community`: free Community Edition (closed source, x86 only); parallel MPP graph, GSQL deep-link queries; same fraud workload.
- [ ] `typedb-single-node`: TypeDB 3 (Rust, MPL 2.0) strongly typed graph with TypeQL and functions; same fraud workload modelled with types and relations.
- [ ] `janusgraph-cassandra`

## Embedded and edge SQL

- [ ] `turso-single-node`: Rust SQLite-compatible engine; inventory transactions, writer conflicts, crash/reopen and compatibility checks against SQLite. Qualify concurrent-write/replication features for the selected release; deferred until the current database batch finishes.
- [ ] `rqlite-docker-compose-cluster`: SQLite replicated over Raft.
- [ ] `litefs-litestream`: SQLite replication and backup to S3.
- [x] [`lancedb/versioned-product-search`](lancedb/versioned-product-search/): 100,000-product embedded/versioned retrieval, single and three-reader serving profiles, pinned snapshots, unindexed-tail visibility, compaction, isolated writer/reader crashes and measured benchmarks. Shared-volume serving is not replicated database HA.
- [ ] `go-embedded-kv-comparison`: LMDB/libmdbx vs Badger vs Pebble vs bbolt; point reads, writes, range scans, write amplification.

## Wide-column

- [ ] `cassandra-docker-compose-cluster`: baseline to compare with ScyllaDB.
- [ ] `hbase-docker-compose-cluster`: HBase on HDFS with ZooKeeper; region splits, compare with ScyllaDB/Cassandra on wide rows.
- [ ] `kudu-impala`: mutable historical sensor data, scans during updates, tablet-leader loss and replica rebuild; compare ClickHouse / TimescaleDB with mutation semantics stated. Kudu supplies storage; Impala supplies SQL.

## Identity and access

- [ ] `keycloak-oidc`: Keycloak realm with OIDC/SAML login; Go and Python apps validating tokens (compare Zitadel/Authentik).
- [ ] `spicedb-vs-openfga`: Zanzibar-style relationship authorization; same schema and checks in SpiceDB and OpenFGA backed by Postgres.
- [ ] `opa-vs-cedar`: policy-as-code authorization; same rules in OPA (Rego) and Cedar, called from a Go service.
- [ ] `teleport-single-node`: short-lived certs for SSH, Postgres and k8s access with session recording.

## Observability pipeline

Plan and source links: [`observability/README.md`](observability/README.md), including
[the requested ClickHouse comparison article](https://clickhouse.com/resources/engineering/top-infrastructure-monitoring-tools-comparison).

- [ ] Self-hosted stack matrix: ClickStack vs Victoria stack vs GreptimeDB vs OpenObserve vs SigNoz vs Grafana (Prometheus baseline; named Loki/Mimir/Tempo scale-out profile). Reuse the shared corpus and independent completeness/recovery oracle.
- [ ] Optional hosted free-tier checks: Honeycomb and Better Stack; Chronosphere only after no-cost access is confirmed. These are not self-hosted OSS editions. **Remind Muzammil to set up a Honeycomb free account before that comparison.**
- [ ] `nagios/`: GPLv2 Nagios Core host/service checks, plugin-based fleet monitoring, detection/recovery and alert accounting under injected failures; exclude commercial XI features.
- [ ] `zabbix/`: agents/proxies, discovery, supported server HA and database recovery; compare the same host/service fault schedule with Nagios Core and the modern stacks.

- [ ] **After the current recovery backfills:** fair GreptimeDB vs Victoria stack
  comparison using [the comparison contract](observability/README.md#greptimedb-vs-victoria-after-the-recovery-backfills).
  Match machine class, per-node and whole-stack budgets and durability. Replay
  identical input/queries; compare single versus batched ingestion and supported
  native/protocol/query approaches, with separate cold/warm cache measurements.
  Run at least three repetitions in alternating order; compare sustained load,
  visibility, storage and live-fault recovery. Existing
  VictoriaMetrics/VictoriaLogs baselines are not a complete stack qualification.
- [ ] Main three-stack comparison: **GreptimeDB vs VictoriaMetrics + VictoriaLogs + VictoriaTraces vs Prometheus + Loki + Tempo**. One instrumented application/corpus, matching event times/IDs/tenants/retention/sampling, independent sample/log/span-body oracle, supported common queries, and sequential whole-stack resource budgets. Measure ingestion admission versus durable post-crash visibility, warm/cold query latency, storage, compaction/backpressure and recovery. Keep standalone/HA/distributed differences explicit; Prometheus HA is not native database sharding. Cortex/Mimir extensions remain separate. Full three-signal comparison is future work beyond the current metrics/logs baseline.

- [ ] Standalone system examples for VictoriaMetrics, VictoriaLogs and VictoriaTraces: supported single-node, Compose cluster and Kubernetes deployments; clustered high-cardinality metrics, structured logs and deterministic traces respectively. Replication/dedup, out-of-order ingestion, storage replacement and completeness checks; compare GreptimeDB on each matching signal, plus Prometheus/Loki/Tempo. The current Greptime comparison baselines are not full independent system qualification.
- [ ] `mimir/`: multi-tenant remote write and historical S3 queries, ingester/ingestion-path failure and compaction/store recovery; compare Cortex / VictoriaMetrics / GreptimeDB on identical samples. Pin the selected ingestion architecture.
- [ ] `cortex/`: same multi-tenant samples and PromQL oracle as Mimir; ingester quorum loss, WAL replay and historical-block recovery.
- [ ] `thanos/`: deduplicate HA Prometheus scrapes, query historical S3 blocks, test compactor/store-gateway loss; distinguish scrape HA from database sharding.
- [ ] `victoriatraces/` and `jaeger/`: trace-ID and supported query comparisons with Tempo, deterministic OTLP spans, collector/storage failure and explicit sampling/completeness accounting. Qualify Jaeger's chosen persistent backend; do not assume experimental backends are production-ready.
- [ ] `openobserve/`: Rust unified observability engine; single-node, supported Compose/kind cluster and application telemetry workload with SQL/log search and metrics/trace correlation. Native compaction, ingester/querier loss and object-store outage; compare GreptimeDB / Victoria stack / Grafana stack. Check license/edition and release stability before implementation.
- [ ] `signoz/`: ClickHouse-backed OTel application monitoring, correlated telemetry, collector/ClickHouse recovery and comparison with OpenObserve / GreptimeDB / Grafana stack.

- [ ] `prometheus/`: single-node baseline, supported Compose/kind monitoring deployments and a clustered fleet-metrics workload. Compare matched PromQL/range queries and ingestion/visibility with GreptimeDB and VictoriaMetrics; separate native Prometheus HA scraping from Cortex/Mimir-backed distributed long-term storage. Do not claim native database sharding from multiple independent scrapers.
- [ ] `loki/`: standalone, distributed Compose and supported Helm/operator deployment; clustered application-log workload with ingestion, tenant/text filters, retention/compaction and ingester/storage recovery. Compare GreptimeDB and VictoriaLogs on identical events, time ranges and durability settings.
- [ ] `tempo/`: standalone and distributed Compose/kind deployment; clustered traces from an instrumented app, trace-ID lookup, TraceQL and ingester/object-store recovery. Compare GreptimeDB tracing only where the chosen release supports matching ingest/query semantics; document sampling and unmatched features.

- [ ] `observability/prometheus-loki-tempo`: a Rust or Go application and worker instrumented through OpenTelemetry Collector, Prometheus metrics, Loki logs, Tempo traces and Grafana correlation by trace ID. Use Garage or SeaweedFS for Loki/Tempo object storage; supported Compose/kind cluster deployments, collector/backend crashes and storage outage/recovery. Track missing/duplicate telemetry against an external request oracle, accounting for sampling and scrape semantics. Compare matched metrics/log queries with the GreptimeDB/Victoria examples; multi-signal correlation is a separate capability. Future example, after the current database batch.

- [ ] `otel-collector-clickhouse`: OpenTelemetry Collector into ClickHouse, visualised in Grafana.
- [ ] `vector-pipeline`: Vector (Rust) log shipping into ClickHouse/Quickwit.
- [ ] `clickstack-otel`: ClickStack (HyperDX) or SigNoz; logs, traces and metrics in ClickHouse via OpenTelemetry.
- [ ] `akvorado-flows`: NetFlow/sFlow/IPFIX collection into ClickHouse through Kafka.

## Rust embedded database backlog

- [x] [Initial candidate research](rust/EMBEDDED_DATABASE_SURVEY.md): Fjall 3.1.13,
  redb 5.0.0 and Turso 0.8.4 documentation and selected source paths reviewed;
  sync/transaction, migration and experimental-feature boundaries recorded.
  The survey itself is research; executed Fjall/redb qualification is separate below.
- [x] [`rust/embedded-databases`](rust/embedded-databases/) durable catalog foundation: native Fjall SyncAll/redb Immediate atomic pairs, independent live Rust actors, owner SIGKILL, retained reopen and byte-checked offline fresh-copy restore. The [100k checkpoint](rust/embedded-databases/RESULTS_100K.json) verifies 101,550/101,561 cumulative acknowledged pairs with zero loss or wrong/partial bodies; phase-local recovered reads, typed native lock rejection and CPU/memory/compression/disk/byte/IOPS evidence are checked. The [million-pair profile](rust/embedded-databases/RESULTS.json) also verifies 1,000,789/1,000,778 ACKed pairs with zero loss/wrong/partial data; one applied redb intent remains unacknowledged. Shared-host functional diagnostics, not a performance winner or SQL replacement.
- [ ] Rust embedded follow-ups: Fjall prefix/range scans during flush/compaction, redb conflicting transactional updates and long-held snapshot space, bounded EIO/full-disk/pressure, format migration, and isolated repeated single/batch/cache comparisons. Add SQLite / RocksDB on matching contracts; no invented clustering.
- [ ] Qdrant extensions: existing single/cluster examples remain covered; add matched-corpus comparisons with Milvus / Weaviate / pgvector plus skew, shard relocation and permanent disk replacement after the current batch.
