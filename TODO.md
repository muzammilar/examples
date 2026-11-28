# TODO

Candidate databases and systems to add as examples. Branch names follow the existing
`<system>-single-node`, `<system>-docker-compose-cluster` and `<system>-kubernetes-{operator,helm}` convention.

## Current priority batch

Flink, Doris and Spark/Hadoop deployment examples, followed by local cross-system pipelines and storage work on Garage, SeaweedFS, CephFS and HDFS. See the [implementation sequence](docs/example-plans/flink-doris-spark-storage.md). MinIO is excluded from this batch. Earlier candidates remain available after this priority.

## Example designs to assign

Start with the [handoff guide](docs/example-plans/README.md), then choose one unchecked workload below. Each design describes the deployment, the event to trigger and how a second observer can tell whether recovery worked. The [system directory index](README.md#systems) lists existing code. The [production-test audit](PRODUCTION_TEST_AUDIT.md) records what actually ran. A design is ready to assign once its local release, license and topology prerequisites are confirmed.

| Area | Design backlog |
|---|---|
| PostgreSQL, MySQL, OLAP, HTAP and wide-column stores | [Orders, shard moves, analytical corrections and cell-level access](docs/example-plans/relational-and-analytical.md) |
| Time-series, streaming SQL, CDC, queues, workflows and observability | [Event replay, external effects and signal completeness](docs/example-plans/streaming-and-observability.md) |
| Document, search, KV, coordination, graph, edge and identity | [Elections, visibility, fencing, traversal and revocation](docs/example-plans/data-services.md) |
| Lakehouse, object storage, HPC and embedded engines | [Publication, compaction, replication, fencing and restore](docs/example-plans/storage-and-lakehouse.md) |
| Systems with existing deployments | [New workload questions using the labs already on main](docs/example-plans/existing-systems.md) |

**Suggested first assignments** (independent, in addition to the accepted [GreptimeDB versus Victoria comparison](observability/README.md#greptimedb-vs-victoria-planned-comparison)):

1. **MongoDB change streams** — one local replica set, one election, one persisted resume token. Deliver the recovered change stream and reconcile it against majority-acknowledged source operations.
2. **Temporal payment retry** — one worker crash between a local payment commit and activity completion. Deliver a history showing workflow attempts and the charge count for every order. Reuse this fixture later for Restate and Inngest.
3. **Pinot delivery tracking** — start with just the upsert table. Show which status wins for each delivery after a duplicate, a late update and server recovery. Give star-tree aggregation its own append-only table.
4. **Apache Ozone S3** — reuse the object corpus and distinguish a metadata election from loss of a DataNode. Deliver full-body readback and native replica-placement evidence for both phases.
5. **SlateDB writer fencing** — start two Rust processes over a lab-owned object store and resume the former writer after handoff. Deliver its actual error and the durable records visible to a fresh reader.

For each assignment, start with one supported topology, a deterministic fixture, healthy operation, one failure and a fresh independent readback. Publish the commands, measured effects and remaining gaps beside the example. Preserve failures. Use the [repository guide](AGENTS.md#progressive-testing-across-systems) for later deployment shapes and production tests. Any performance campaign needs a separate depth choice.

## Break and recover: all existing and future examples

**Current scope (2026-11-23):** experiments are paused. Preserve the completed
results and failed findings, then continue breadth-first exploration when asked.
The deferred tests linked below are future work; they do not require exhausting
CedarDB, Rayforce, ZFS or DuckDB/Iceberg before exploring another system.
**GreptimeDB versus Victoria is an accepted planned comparison** with its own
bounded scope and user-selected performance depth.

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

## Deferred follow-ups for covered systems

- [ ] **GreptimeDB versus Victoria:** run the [planned fair comparison](observability/README.md#greptimedb-vs-victoria-planned-comparison).
  Match hardware and complete-stack budgets, workload/durability contracts,
  single versus batched/native ingestion and cold versus warm queries. Report
  correctness, recovery, latency, CPU, memory, compression, disk space and disk
  read/write bytes/IOPS. Select performance depth before running it; completion
  of every optional production-test backfill is not a prerequisite.
- [ ] **CedarDB:** [planner/statistics preparation, ingestion/cache variants,
  pressure, interrupted backup and supported upgrades](cedardb/backup-recovery/README.md#future-work--todo).
- [ ] **Rayforce/DuckDB:** [optional integrity diagnosis, journal/snapshot/restore
  cases and selected future comparisons](rayforce/duckdb-comparison/README.md#future-work--todo).
- [ ] **ZFS:** [independent archive restore, replication/ACK observation,
  guest persistence and bounded pressure](zfs/storage-lab/README.md#future-work--todo).
- [ ] **DuckDB/ZFS:** [supervised deadlines, dependency/restore and retention
  checks](duckdb/zfs-recovery/README.md#future-work--todo).
- [ ] **Recent distributed examples:** use each system README's **Future work / TODO**
  index and each example's concrete checklist for the remaining partition/quorum,
  scaling, upgrade, pressure and independent-restore cases. See the
  [coverage audit](PRODUCTION_TEST_AUDIT.md#controllerworkload-findings-and-required-backfills)
  for measured context; do not rerun already-qualified cases mechanically.

## Already covered

Use the [root inventory](README.md#systems), [analytical examples](README.md#analytical-database-follow-ups), [Rust embedded results](rust/embedded-databases/), and [recent coverage audit](PRODUCTION_TEST_AUDIT.md) to check deployment and test status. The top-level [PostgreSQL](postgres/) and [MySQL](mysql/) directories include replicated and sharded deployments. Tigris has a research directory only.

## Top priority

The first five priority systems now have local examples. Each README describes the tested edition, deployment and comparison scope.

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
- [ ] Extend the existing Citus and PgDog sharding cases and evaluate Multigres's supported routing. Compare Neon separately for disaggregated storage and read scaling. [PostgreSQL designs](docs/example-plans/relational-and-analytical.md#postgresql-and-its-extensions)
- [ ] Add Vitess online resharding, ShardingSphere and PolarDB-X to the customer-order comparison. NDB and fixed-shard Vitess already have measured labs. [MySQL designs](docs/example-plans/relational-and-analytical.md#mysql-and-its-ecosystem)
- [x] pgvector
- [x] [StarRocks](starrocks/): single-node, Compose comparison and official operator; mutable-order correctness, benchmarks and 13 operator production cases, with failed attempts and recovery limits documented.
- [x] Iceberg + Trino + MinIO
- [ ] MongoDB replica set
- [ ] Temporal
- [ ] Restate
- [x] [JuiceFS metadata/S3 dependency recovery](juicefs/dependency-recovery/): live C++ publication, cross-mount locks, exact ACK/native RF3 checks and explicit ambiguous errors; rejected probes retained. Metadata HA, CSI and independent restore remain future work.
- [x] SeaweedFS — [replicated S3 and disk recovery](seaweedfs/s3-cluster/)

## Time-series

Assign from the [time-series designs](docs/example-plans/streaming-and-observability.md#time-series) and the [VictoriaMetrics cluster design](docs/example-plans/streaming-and-observability.md#observability).

- [x] `timescaledb-single-node`: hypertables and continuous aggregates on Postgres.
- [x] `questdb-single-node`: ILP ingest and `SAMPLE BY` queries.
- [ ] `victoriametrics-docker-compose-cluster`: vminsert/vmselect/vmstorage as Prometheus long-term storage.
- [x] `influxdb3-core-single-node`: Arrow/Parquet engine with SQL.
- [x] [`greptimedb`](greptimedb/): single-node/Compose lifecycles and fresh operator recovery matrix passed; million samples + million logs per backend, ten operator cases and 207,000 ACK rows reconciled. Found and documented failed WAL initialization causing 3,400 lost samples; explicit repair, bootstrap guard and dual-network restart fix tested. Mimir/Cortex and tracing comparisons remain future work.
- [ ] `tdengine-docker-compose-cluster`: IoT time-series (AGPL); one table per device with super tables, vnodes sharding; rerun TSBS against TimescaleDB/QuestDB/InfluxDB.
- [ ] `iotdb-docker-compose-cluster`: Apache IoTDB (Apache 2.0) ConfigNode/DataNode cluster, TsFile columnar storage; same TSBS run.
- [ ] `dolphindb-single-node`: free Community Edition (memory/core caps); columnar time-series engine with its own scripting language, used for tick data; compare with kdb+ on the same market data.
- [ ] `arcticdb-local`: reopen versioned market-data snapshots after corrections using LMDB and an isolated local S3-compatible service. Check symbol-level write coordination in the selected build.

## Streaming databases and CDC

The [streaming SQL and CDC designs](docs/example-plans/streaming-and-observability.md#streaming-sql-and-cdc) specify event-time, changelog and replay checks.

- [x] `risingwave-single-node` (top priority): Postgres-compatible incremental materialized views with built-in CDC.
- [ ] Materialize emulator for local view logic and a separately licensed self-managed kind profile for deployment/recovery. Retract a sale from the old group after its customer's region changes.
- [ ] `feldera-single-node`: incremental SQL retractions and clean replay from retained input in Community. Native checkpoint/fault tolerance requires Enterprise.
- [ ] Apache Flink keyed-state and event-time timer recovery during TaskManager loss and rescaling.
- [ ] Apache Fluss replicated primary-key inventory with independent changelog reconstruction after a bucket leader fails.
- [ ] `timeplus-proton-single-node`: C++ streaming SQL on ClickHouse code (Apache 2.0); streaming joins and windows from Kafka/Redpanda; compare with RisingWave.
- [ ] `debezium-postgres-kafka`: CDC from Postgres into Kafka, then ClickHouse.
- [ ] `debezium-mysql-kafka-flink-clickhouse`: end-to-end e-commerce CDC pipeline, MySQL → Debezium → Kafka → Flink → ClickHouse, orchestrated by Airflow.
  - **Source:** MySQL 8 with `binlog_format=ROW`, `binlog_row_image=FULL`, GTIDs on; `demo` schema with `products`, `orders` (`pending`/`completed`/`canceled`), `order_items` (price and cost price for margin); `debezium` user with `REPLICATION SLAVE`/`REPLICATION CLIENT`/`RELOAD`/`SHOW DATABASES`; seed data plus a load generator for inserts/updates/deletes.
  - **Capture.** Preserve Debezium's change envelope and source metadata from the initial snapshot through binlog streaming. Use a local registry compatible with the selected Flink decoder and retain the schema-history topic. Place any flattened representation on a separate branch for consumers that request it. [Flink Debezium format](https://nightlies.apache.org/flink/flink-docs-stable/docs/connectors/table/formats/debezium/)
  - **Kafka, two variants:** Docker Compose (KRaft Kafka + Kafka Connect + Schema Registry, connector registered by `curl` to `:8083/connectors`) and Kubernetes with Strimzi (`Kafka` with KRaft node pools, `KafkaConnect` with a Debezium plugin image built via `spec.build`, `KafkaConnector` CR for the MySQL source, `KafkaTopic` CRs for per-table topics).
  - **Processing.** Decode inserts, before/after updates and deletes in Flink. Join the changing tables on stable keys and emit replacements/retractions for cancelled orders and corrected prices. Preserve source offsets with checkpoints and establish the sink's replay contract independently. [Flink changelog handling](https://nightlies.apache.org/flink/flink-docs-stable/docs/connectors/table/formats/debezium/)
  - **Sink.** Write ordered, versioned results and delete markers per order to ClickHouse. Compute daily revenue from the latest surviving order states. An insert-triggered materialized view sees inserted blocks, so summing successive order versions double-counts price changes and cancellations. Compare raw Kafka ingestion using the same latest-state query and crash after sink acceptance to test duplicates. [ClickHouse incremental views](https://clickhouse.com/docs/materialized-view/incremental-materialized-view)
  - **Airflow:** DAGs that apply MySQL migrations (`migration_history` table), register/pause/restart connectors through the Connect REST API (or apply Strimzi CRs), trigger Debezium incremental snapshots via a signal table for backfills, and run nightly row-count/sum reconciliation between MySQL and ClickHouse.
  - **Ops:** Kafka UI (Conduktor, Redpanda Console or AKHQ) to browse topics and schemas; show a schema change (add column) flowing through the schema-history topic and Schema Registry compatibility rules; failure demos: restart Connect mid-stream, drop and recreate a Flink job from a savepoint.

## Message streaming and queues

- [ ] Apache BookKeeper: isolate ledger-writer fencing and recovery at declared write/ACK quorums. [Storage-engine design](docs/example-plans/storage-and-lakehouse.md#embedded-engines-and-memory-grids)

The [messaging designs](docs/example-plans/streaming-and-observability.md#message-streaming-and-queues) specify the acknowledgement and replay boundary for each system.

- [x] `redpanda-single-node` / `redpanda-docker-compose-cluster` (top priority): Kafka API without JVM or ZooKeeper; compare with the Kafka examples.
- [ ] Apache Iggy single-node offset replay and three-node replicated partition recovery under a live producer and consumer. [Upstream clustering release](https://iggy.apache.org/blogs/2026/09/21/release-0.9.0/)
- [ ] `fluvio-single-node`: Rust streaming with WASM SmartModules.
- [ ] Apache Pulsar standalone control and separate clustered BookKeeper/broker failure and tiered replay using local storage.
- [ ] `rabbitmq-streams`: match replicated quorum queues with streams for leader-loss and replay tests. Keep classic queues as a single-copy control.
- [ ] `aeron-cluster`: recover a deterministic order book from a snapshot and replicated log. Measure the time to a committed service reply separately from UDP/IPC transport latency.
- [ ] KurrentDB single-node optimistic concurrency and persistent subscription redelivery. Multi-node deployments from 26.2 require Enterprise and remain conditional. [Upstream licensing](https://kurrentdb.kurrent.io/blog/licensing-in-kurrentdb-v26-2-and-beyond-what-s-free-and-what-s-licensed/)

## Durable execution and workflow engines

The [durable-execution designs](docs/example-plans/streaming-and-observability.md#durable-execution) use a local payment ledger to distinguish workflow replay, external retries and duplicate effects.

- [ ] `temporal-docker-compose-cluster`: workflows, activities, retries, signals; Go and Python workers backed by Postgres.
- [ ] `restate-single-node`: Rust durable execution runtime; virtual objects, durable promises, idempotent handlers.
- [ ] `inngest-self-hosted`: self-hosted Inngest server with Postgres + Redis; steps, retries, throttling, fan-out (complements `js/inngest-js` and `python/inngest-py`).
- [ ] `hatchet-single-node`: Postgres-backed task queue and DAG workflows.
- [ ] `dbos-postgres`: durable workflows as a library on top of Postgres.
- [ ] Comparison example: same order/payment saga implemented in Temporal, Restate and Inngest.

## Postgres and MySQL

[PostgreSQL](postgres/) already includes Citus, PgDog, Neon, pgvector and customer sharding. [MySQL](mysql/) includes Vitess, NDB and customer sharding. The older related examples below remain in their original directories. See the [relational experiment designs](docs/example-plans/relational-and-analytical.md) for the proposed extensions and comparisons.

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

Use the same orders and customer keys for the [PostgreSQL designs](docs/example-plans/relational-and-analytical.md#postgresql-and-its-extensions). Check shard placement on sharding systems and compute/storage behavior on Neon.

- [ ] `postgres-single-node`: plain PostgreSQL on the current stable major, with asynchronous I/O, `pg_stat_statements` and `pgbench`. Pin the release at implementation. [Release policy and current versions](https://www.postgresql.org/support/versioning/)
- [ ] Extend the existing [Patroni HA group](postgres/pgdog-docker-compose-cluster/) with DCS/quorum loss, peer partitions, stale-primary fencing and independent backup/restore. Primary failover, replica crashes and synchronous-standby stalls already have measured results.
- [ ] `postgres-kubernetes-operator`: CloudNativePG on kind; replicas, switchover and a point-in-time restore to a second cluster via the supported Barman Cloud plugin and an isolated local object store.
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

Extend the [existing MySQL labs](mysql/) with the same order corpus. The [MySQL briefs](docs/example-plans/relational-and-analytical.md#mysql-and-its-ecosystem) specify separate routing, replication and operator cases.

- [ ] `mysql-single-node`: current supported stable MySQL baseline with witnessed InnoDB transactions and a separate `sysbench` workload. Retain 8.4 as an explicit compatibility profile where required.
- [ ] `mysql-docker-compose-cluster`: InnoDB Cluster (Group Replication) + MySQL Router; single-primary `failover`.
- [ ] `mysql-kubernetes-operator`: Oracle MySQL Operator for InnoDB Cluster on kind. A Percona XtraDB Cluster operator scenario needs its own Galera failure and replication expectations.
- [ ] `mariadb-galera-cluster`: virtually synchronous multi-primary replication. Check certification conflicts, replica apply lag, minority behavior and state transfer.
- [ ] `proxysql-read-write-split`: query routing, read/write split and query rules in front of replicas.
- [x] `vitess-docker-compose-cluster`: unsharded deployment, 3 MySQL copies, VTGate/VTctld/etcd/VTOrc, semi-sync primary failure and million-order loss tracking.
- [ ] Extend Vitess deployment with online `Reshard` workflow and scaling measurements.
- [ ] `vitess-kubernetes-operator`: PlanetScale `vitess-operator` on kind.
- [x] `ndb-cluster-docker-compose-cluster`: Community 8.4.11; 3 partitions × 3 copies, data/SQL/management failures, loss tracking and Vitess architecture comparison. Separate correlated disk-full crash lost 6,800 acknowledged orders.
- [ ] `shardingsphere-proxy`: Apache ShardingSphere-Proxy in front of plain MySQL (and Postgres) shards; sharding rules, read/write split, distributed transactions.
- [ ] `polardbx-kubernetes-operator`: PolarDB-X (Apache 2.0) on kind; CN/DN/GMS/CDC split, partitioned tables, global secondary indexes.
- [ ] `matrixone-docker-compose-cluster`: MySQL-compatible HTAP (Apache 2.0, Go); separated storage/compute on isolated S3-compatible storage, CN/TN/LogService; OLTP plus TPC-H on one copy of the data.
- [ ] Comparison example: same `orders` workload on single MySQL vs Vitess vs ShardingSphere vs PolarDB-X vs NDB/RonDB vs TiDB (existing example); then Vitess vs Citus/PgDog/Multigres for the MySQL-vs-Postgres sharding story.

## In-memory OLTP

- [ ] `volt-active-data`: partitioned single-threaded in-memory SQL with stored procedures (H-Store lineage); gate local testing on the available Developer Edition license, supported image architecture and permitted K-safety topology. [Local developer options](https://developers.voltactivedata.com/get-started)
- [ ] `spacetimedb-single-node`: transactional Rust modules and client subscriptions for multiplayer world state; reconnect/replay and crash recovery versus PostgreSQL + an application/WebSocket service. Qualify current license and native replication support; deferred until the current batch finishes.

## Real-time OLAP

- [ ] [StarRocks shared-data follow-up](docs/example-plans/relational-and-analytical.md#real-time-olap): replace a compute node and refill its cache from local object storage. The [shared-nothing single-node, Compose, operator and mutable-orders comparison](starrocks/) already ran.
- [ ] `doris-docker-compose-cluster`: mutable orders/inventory joins, replicated tablets versus storage/compute separation, compute isolation and disk rebuild; compare StarRocks / ClickHouse on identical data.
- [ ] `pinot-docker-compose-cluster`: Kafka delivery-status upserts with duplicate/out-of-order events, segment recovery and server loss. Use a **separate append-only table** for star-tree aggregates. Pinot rejects star-tree indexing on an upsert table. [Upsert limitations](https://docs.pinot.apache.org/build-with-pinot/ingestion/upsert-dedup/upsert)
- [ ] `druid-docker-compose-cluster`: segments, ingestion specs.
- [ ] `databend-single-node`: Rust cloud warehouse on object storage.
- [ ] `firebolt-core-single-node`: free self-hosted Firebolt engine; compare with ClickHouse and StarRocks.
- [ ] `exasol-docker-evaluation`: use the vendor’s separate Docker evaluation image, capped at 10 GiB of raw data. Exasol Personal uses Exasol Launcher. Verify image platform and isolation prerequisites before scheduling. [Edition differences](https://docs.exasol.com/db/latest/get_started/exasol_editions.htm)
- [ ] `vertica-local-trial`: examine projection locality and node recovery only with an available local evaluation license and permitted topology. The former permanent Community Edition limits do not apply to new versions. [Current trial and CE upgrade restriction](https://docs.rocketsoftware.com/bundle/vertica_26.2/page/getting-started/community-edition-ce.html)
- [ ] `cratedb-docker-compose-cluster`: industrial telemetry plus text filters, tenant skew, three shards with three copies, relocation and disk loss; compare GreptimeDB / TimescaleDB / Elasticsearch on matching query subsets.
- [ ] `heavydb-single-node`: HeavyDB (formerly OmniSciDB/MapD, Apache 2.0) GPU-accelerated SQL; use a supported local container for the CPU profile and a separately identified compatible GPU host for the GPU profile.

## Local analytical engines and storage

[Analytical process and storage designs](docs/example-plans/relational-and-analytical.md#analytical-processes-and-local-storage) cover embedded DuckDB, separate database servers, analytical libraries and the ZFS filesystem. Deployment and recovery boundaries differ across those cases.

- [x] `cedardb/backup-recovery`: synchronous million-row fixtures versus PostgreSQL, live independent actors, graceful/SIGKILL recovery, online physical backups, read-only fresh-copy restores, full ACK/body/history reconciliation and CPU/memory/compression/I/O/storage observations. CE privileges/async-commit and PostgreSQL replication-HBA gotchas documented.
- [x] `cedardb/backup-recovery` analytical contention: [12 sustained cells](cedardb/backup-recovery/projects/analytical-contention/docs/HTAP.md), three fresh-process trials per backend/traffic condition, 60 s warmup and 300 s monotonic measurement deadlines. Complete bodies and 109,580 historical ACKs verified, zero missing. Median CedarDB query throughput was 3.33× PostgreSQL alone and 2.69× with live appends/readers; PostgreSQL used less charged memory. Resource costs and the documented 2.91 ms UTC timing discrepancy remain in the report. This immutable subset plus live appends does not cover UPDATE/DELETE contention or cold-cache behavior.
- [x] CedarDB/PostgreSQL [mutable workload foundation](cedardb/backup-recovery/projects/transactional-mutations/docs/MUTABLE.md): Go paired UPDATE and DELETE/reinsert, transactional receipts, independent snapshot readers, exact body/version/aggregate oracle and native read-only/rollback controls. Four million-row healthy/SIGKILL cases verify **2,972 ACKed transactions**, zero acknowledged loss or wrong/partial rows; 23 controller tests and fresh million-row driver controls pass. Earlier 10,000-row cases and short diagnostics remain separate. CPU/memory/compression/disk/byte-I/O and native IOPS recorded; preserve the PostgreSQL CASE failure and invalidated prototype, archived before fixture replacement. The [12-cell sustained million-row comparison](cedardb/backup-recovery/projects/transactional-mutations/docs/MUTABLE_BENCHMARK.md) passes: 108,440 ACKs and 83,243 complete query results, zero loss/corruption; CedarDB median query throughput 3.29×/3.03× alone/live, PostgreSQL lower memory. Automatic-statistics preparation and warm-query scope remain explicit.
- [ ] CedarDB follow-up: add explicitly prepared planner statistics and captured query plans rather than inferring readiness in every case from automatic analysis; repeated single/batch/bulk and cache-temperature comparisons; bounded disk-full/pressure (including the observed `db.ballast`/WAL footprint), interrupted backup and supported upgrade paths. A single-pod kind StatefulSet may test deployment/PVC behavior; native CedarDB clustering/HA remains unsupported by this example.

- [x] `rayforce/duckdb-comparison`: analytical queries plus single/batch query and typed append paths through Python/C++/Rust; 144 ingestion trials, full old/new body verification, CPU/memory and disk accounting. In-memory, single-writer scope.
- [x] Extend that comparison with DuckDB columnar data-chunk Appender: 72 correctness cells and 144 fresh-process performance trials, per-batch query-visible acknowledgements, identical inputs and full-body verification.
- [x] Rayforce/DuckDB saved snapshots: matching two-million-trade/10,000-customer fresh-process reopens, exact full-body/group/join checks, native file/encoded-column/codec evidence, sampled disk growth and CPU/memory/I/O costs. One functional snapshot observation per engine; no capacity or live recovery claim.
- [x] Rayforce synchronous native IPC journal recovery: two live-writer/reader SIGKILL cycles, second snapshot plus tail, external fsynced witness, exact two-million-trade/10,000-customer and all-ACK reconciliation, CPU/memory/disk/I/O/compression evidence. Single-process correctness, not capacity or HA.
- [x] Rayforce fenced snapshot/tail restore into separate owned local files with the original input unavailable to the database: zero replay errors, full seed/customer/ACK proofs and measured CPU/memory/disk/I/O/compression. Same-host offline restore, not live backup or independent-host durability.
- [x] Rayforce cross-container journal writer exclusion under live traffic, plus two-crash recovery on owned Linux volumes. Ten second-open attempts rejected, exact full-body/ACK reconciliation, native block I/O measured. Preserve/document the failed host-share ownership case and standard-library filesystem controls; do not generalize its unsupported locks to all bind mounts.
- [x] Rayforce controlled torn-tail diagnostic: refuse startup, validate the intact prefix, repair only the exact known three-byte trailer in a fresh copy, and restore all seed/customer/ACK bodies. Preserve original/damaged files; the six-case recovery profile creates its own source. Offline format recovery, not real device failure or general corruption repair.
- [x] Rayforce [interior payload-corruption evaluation](rayforce/duckdb-comparison/projects/rayforce-journal/docs/CORRUPTION.md): invalid types fail closed, but a valid one-bit price edit replays **one wrong acknowledged body — FAILED integrity**. Untouched backup restores all 2,000,000 seed trades, 10,000 customers and 2,269 ACKs correctly. The full production target propagates the failure; logical file-edit fault model, not physical bit rot or normal crash loss. Resource/storage costs and failed state retained.
- [x] Rayforce [middle-header corruption](rayforce/duckdb-comparison/projects/rayforce-journal/docs/HEADER_CORRUPTION.md): native startup rejects one-bit magic/version damage, but applying its prefix-truncation hint to another copy loses **566 ACKs** with clean startup. Guard rejects both; untouched backup recovers all 2,000,000 seed/10,000 customer/2,259 ACK bodies. Preserve unsafe recovery as **FAILED durability** under the logical file-edit fault model, with CPU/memory/compression/disk/I/O costs.
- [x] Rayforce [native AOF API diagnostic](rayforce/duckdb-comparison/projects/rayforce-aof/docs/AOF.md): C++ commit/scan application, 10,000 × 1,024-byte opaque events, independent full-body/witness checks and all CPU/memory/compression/disk/I/O costs. Payload edits reject; length/commit-marker edits silently omit **5,000 / 5,000 / 100 ACKs — FAILED integrity** under the offline file-edit model. Known uncommitted trailer and intact-backup verification pass. Buffered append and commit timings have separate scopes; no ordinary crash or automatic table-persistence claim.
- [x] [DuckDB native live WAL recovery](rayforce/duckdb-comparison/projects/duckdb-recovery/docs/DUCKDB_RECOVERY.md): signed Quack C++ TLS sessions, two retained-volume SIGKILLs, live readers/writers, checkpoint under load, full two-million-trade/10,000-customer and 2,364-ACK reconciliation with zero loss/corruption. CPU/memory/compression/disk/block-I/O and recent-versus-historical read timings recorded. Full serial production suite preserves both known Rayforce integrity failures; final five-command lifecycle passes. Functional diagnostic, not HA/capacity or a recovery-performance ranking.
- [x] Rayforce [AOF segment recovery boundaries](rayforce/duckdb-comparison/projects/rayforce-aof/docs/AOF_SEGMENTS.md): 159 native segments, complete 10,000/10,003-body rotation/reopen/backup checks, exact known three-byte tail handling and full-scan rejection of sealed CRC damage. Native open alone misses sealed corruption; deleting the tail loses 46/49 ACK bodies and the external witness rejects. CPU/memory/compression/disk/I/O and per-operation timings recorded. Expected-loss diagnostic, not a pass for existing strict integrity failures, physical disk loss or live availability.
- [ ] Rayforce/DuckDB: repeated alternating recovery-performance trials, asynchronous journal/watermark recovery, interrupted snapshot replacement, compressed journal frames, AOF live concurrent scan/process-crash and interrupted rotation, missing first/interior segments, online backup/restore, pressure, prepared SQL and genuinely cold persisted-query matrices.

- [x] [`duckdb-zfs-recovery`](duckdb/zfs-recovery/reports/FULL_SUITE_RESULTS.json): fresh five-command lifecycle passes; continuous guest-crash recovery verifies 2,000,000 seed + 323,000 acknowledged rows and 180 independent reads with zero missing/wrong/partial bodies. The full disk/crash/replica/archive sequence verifies 7,453,000 exported bodies across copies and preserves all 30,000 source acknowledgements absent from the asynchronous standby. Effective 256 MiB ARC persists across reboot; all owned resources are removed. CPU/memory/compression/disk/I/O evidence and earlier failures remain explicit. Quack overrides the requested HTTP timeout, so no three-second RPC deadline, capacity comparison or complete production qualification is claimed.
- [ ] DuckDB/ZFS deeper pass: isolate Quack connection/query/fetch/close timings and qualify a supervised deadline with ambiguous-write reconciliation; add further dependency/pressure/restore and retention cases. Keep physical host-loss durability and repeated fair recovery-performance comparisons unqualified.
- [x] [ZFS storage recovery follow-up](zfs/storage-lab/): default 4 MiB/s five-command lifecycle, full mirror/RAIDZ2/replication suite, 157/140 complete immutable-body reads during recovery, verified 512 MiB payloads, clean scrubs, guarded native pool archives and scoped teardown. Thirty-five controls pass; CPU/memory/compression/disk-byte/IOPS observations published. Earlier explicit-rate checkpoint and archive-checker/Python-prerequisite failures remain separate; stream validation is not restore qualification.
- [ ] ZFS deeper pass: restore the preserved native streams, add continuous replication observation and per-flush external acknowledgements, and exercise guest restart/configuration persistence, bounded pressure and further loss combinations. Deferred while the repository proceeds breadth-first; select any deeper run explicitly.

- [ ] `hyper-api-local`: a client-managed Tableau Hyper process over local extract files. Pick a current Linux container wheel that matches the architecture (0.0.27426 ships Linux ARM64 and x86-64), then compare transaction recovery and exact analytical results with DuckDB. [Published artifacts](https://pypi.org/project/tableauhyperapi/0.0.27426/)
- [ ] `polars-datafusion`: Polars and DataFusion on the same Parquet files as the DuckDB example.
- [ ] `monetdb-single-node`: a MonetDB column-store server running the same analytical queries as DuckDB, with a hot-backup/fresh-restore case.

## Lakehouse and object storage

- [ ] `rust/columnar-formats`: compare **CSV, Parquet, Arrow IPC/Feather V2 and Vortex** on the same deterministic rows, typed full-body oracle and resource budgets. Measure compression, encode/decode/query latency, CPU/memory, disk space/read-write bytes/IOPS, verified cold versus warm reads and interrupted/corrupt-file recovery. Planned, unrun; [design and compatibility requirements](docs/example-plans/columnar-formats.md).

Pick a [table, catalog or storage design](docs/example-plans/storage-and-lakehouse.md) with a concrete commit/restore boundary. The checked entries below keep their original experimental scope.

- [ ] Apache Hudi: compare copy-on-write and merge-on-read tables on identical order corrections. Interrupt compaction and reconcile both snapshots at the same source offset using each table's own completed instant.
- [ ] Apache Ozone: run the S3 gateway over replicated DataNodes and persistent metadata. Separate an Ozone Manager election from DataNode loss and verify multipart object bytes and rebuilt placement.

- [ ] [`tigris/`](tigris/): **self-hosted only**, as requested. Current official
  docs/public repositories did not reveal a supported self-hosted object-storage
  server (2026-11-22); blocked on a real server distribution/source and deployment
  terms. The public SDK and TAG caching proxy are not that server. Do not create
  managed buckets or request cloud credentials as a substitute. Once available,
  compare with Garage, SeaweedFS, RustFS and Ceph RGW.
  Use identical bodies, checksums, request mixes and client budgets; measure
  single/multipart upload, range reads, conditional writes, visibility and
  interrupted-upload recovery under independent live writers/readers.
  Managed deployment is outside this scope.
  Do not attribute local SeaweedFS observations to Tigris. Planned, not run.

- [ ] `hoglake-local`: [PostHog Hoglake](https://github.com/PostHog/hoglake), a Postgres-native lakehouse catalog; investigate its native Trino connector and catalog recovery. Documentation backlog only; no implementation in the current Iceberg + Trino + MinIO work.
- [x] `iceberg-trino-minio`: persistent Iceberg REST fixture + 4 MinIO nodes + Trino 484 coordinator/3 workers; million-row reconciliation, worker task retry, storage-node failure and catalog restart.
- [x] `iceberg/multi-system`: five SeaweedFS-backed DuckDB/ClickHouse data paths, Spark compaction, safe archive handoffs and a three-shard/three-copy native serving cache with replica recovery.
- [x] [Iceberg live storage-crash qualification](iceberg/multi-system/projects/cold-storage-recovery/reports/LIVE_RECOVERY_RESULTS.json): two live writers and two independent readers, 2,000,000 seed rows / 10,000 customers; corrected crash case verifies 2,490,000 acknowledged rows per engine and freshly rechecks the earlier 2,480,000-row table. Snapshot-bound compression, CPU/memory/disk/I/O, latency and original instrumentation failures are recorded. Guarded teardown and offline closed-history checks pass; unrelated containers are unchanged. This is one-host retained-volume mini recovery, not HA, disk loss or capacity qualification.
- [x] [Iceberg short-lived tool resource reporting](iceberg/multi-system/projects/cold-storage-recovery/reports/RESOURCE_REPORT_RESULTS.json): all five functional paths produce 22 native tool reports plus Docker lifetime events; Spark observes Python/JVM separately. Corrected coarse-tick CPU aggregation replays original samples; its rebuilt image and 15 native controls pass. Cleanup preserves unrelated containers. This is resource-accounting qualification, not a complete capacity benchmark.
- [ ] Current stable DuckDB 2.0.x: actual 2.0.1 C-ABI runner and pinned extensions pass direct ingestion, archive handoff and three-shard/three-copy serving; all nine replicas verify 2.1 million rows. The fresh current profile uses separate images/volumes/results and stable SeaweedFS 4.54, ClickHouse 26.10.10.3, Spark 4.1.4 / Iceberg 1.12.0. ClickHouse-written v2 manifests still omit required Avro `content`; independent inspection confirms the failure, blocking the full matrix. Manifest-I/O faults return SQL errors on 2.0.1 versus historical 1.5.5 SIGABRT. Quack deadlines, WAL recovery, owned-copy file-format compatibility and repeated comparative benchmarks remain unfinished. See [`DUCKDB_VERSIONS.md`](iceberg/multi-system/projects/duckdb-iceberg/docs/DUCKDB_VERSIONS.md).
- [x] [Fresh current Iceberg fault cases](iceberg/multi-system/projects/cold-storage-recovery/reports/CURRENT_RECOVERY_RESULTS.json): retained-volume storage SIGKILL reconciles 2.4 million ACK rows; Spark output interruption/recovery reconciles 3.91 million, with live independent writers/readers. All 207 controls pass. The earlier 3.8-million-row current case remains failed: its final ordered ClickHouse export exhausted 1 GiB native memory with sorting spill disabled. Same-snapshot controls pass with bounded spilling without raising caps; Parquet watermark tuning alone fails. Preserve that failed fixture and finish all-history closure. Current cold restore now verifies all three tables (10.11 million ACK rows per engine), with temporary cleanup and 209 controls passing; retain both operational outcomes. These are single-host functional trials, not a capacity ranking or DuckDB-native compaction qualification.
- [ ] Iceberg cold recovery: reviewed Make checkpoint restores all 146 files into a new owned volume, with full file-hash/type/mode/ownership checks and clean original restart. Independent native pilots restore both witnessed tables (6.06 million ACK rows per engine); concurrent shutdown failed, retained-state serial reader-before-storage shutdown passed. Tracked native Make readback now passes both tables and preserves the failed diagnostic status; its lost-creation-response control also passes. Guarded cleanup and pinned4.53→4.54 cold-upgrade readbacks now pass, with explicit downgrade refusal. Failed-case preservation now passes (333 case artifacts, 54 external artifacts, 17 process fences), as do history verification and application teardown; original-volume/image deletion awaits explicit approval after automatic review rejection. Original retirement remains approval-gated. A separate bounded-memory current compactor test and current-fixture cold restore now pass; the latter replays all 10.11 million ACK rows per engine and retains the failed qualification. Finish current all-history preservation/closure before main-fixture teardown. Cold readback alone does not prove an OOM fix. Preserve archives and both outcomes; see [`COLD_STORAGE.md`](iceberg/multi-system/projects/cold-storage-recovery/docs/COLD_STORAGE.md).
- [x] [DuckDB 2.0.1 native Iceberg baseline](iceberg/multi-system/projects/duckdb-iceberg/docs/DUCKDB_MAINTENANCE.md): actual pinned CLI/extension, 2,000,000 rows / 10,000 customers, 20 → 1 live files; rollback, retained snapshot, no-op and wrong-body controls, plus independent ClickHouse/REST verification. Final Make workflow passes 215 controls in 21.779 s; committed CALL profile 2,149.995 ms. CPU/memory/compression/disk/I/O and the two preserved harness failures are recorded. Healthy functional scope, not a capacity or live-fault qualification.
- [x] [DuckDB native maintenance rejection controls](iceberg/multi-system/projects/duckdb-iceberg/docs/DUCKDB_REJECTIONS.md): native v3, pending-write and pending-schema rejection; unchanged committed metadata/bodies/schema, independent ten-row ClickHouse readback, pinned HTTP settings and final child-process resource capture. Initial Make workflow passes 217 controls; shared-runner regression passes 218. Continuous-append conflicts and live native compactor fault/recovery remain unfinished; these tiny controls are not benchmarks.
- [x] [DuckDB controlled rewrite/append conflict](iceberg/multi-system/projects/duckdb-iceberg/docs/DUCKDB_CONFLICTS.md): stage a rewrite, acknowledge an independent append, verify visibility, reject stale COMMIT with actual snapshot IDs/409, preserve all 110,000 bodies and replan successfully (11 → 1 files); 218 controls and resource/compression/disk/I/O evidence. Continuous-writer progress and million-row native live-fault recovery remain unfinished; this is a bounded transaction diagnostic.
- [x] [Current failed Iceberg case preservation receipt](iceberg/multi-system/projects/cold-storage-recovery/docs/FAILED_PRESERVATION.md): pins original seals and independently restored 3.8-million-row body proof; 76 original source files, 229 original artifacts and 16 native fences, plus final publication/offline replay and 224 controls. Operational verdict remains failed; cleanup-gate integration and native-fixture retirement are unfinished, so teardown stays blocked.
- [ ] Deferred follow-up: **DuckDB SQL client → ClickHouse ↔ Iceberg**. The active [HTTP path](iceberg/multi-system/projects/duckdb-clickhouse/http/docs/DUCKDB_HTTP.md) now uses core httpfs and signed http_client; 2M hot writes/readback and 2.097M retained cold reads pass. The retained fixture rejected the full round trip at its slot guard; a later isolated HTTP fixture passed 2M-row hot/cold/restored verification and teardown. HTTP faults/edge types remain unrun. The following findings are from the preserved native connector. [Initial findings](iceberg/multi-system/projects/duckdb-clickhouse/clickhouse-scanner/docs/DUCKDB_CLICKHOUSE_FINDINGS.md): ordinary/explicit scanner cannot discover the Iceberg catalog table although native pass-through reads it; rollback does not undo remote writes; DateTime64(9) loses sub-microsecond precision. Next pass has five groups: 2M-row complete round trip/types; partial/ambiguous insert and retry; live ClickHouse crash/restart; live S3/catalog outage; concurrent compaction/reconciliation. Existing direct DuckDB–Iceberg results do not qualify this architecture. [Partial insert/retry](iceberg/multi-system/projects/duckdb-clickhouse/clickhouse-scanner/docs/DUCKDB_PARTIAL.md) now reproduces 1,906,688 persisted rows despite a conversion error; blind retry doubles physical rows. Corrected immutable-key replay passes for 2M logical rows under FINAL; transport ACK-loss injection remains unrun. [Live ClickHouse SIGKILL](iceberg/multi-system/projects/duckdb-clickhouse/clickhouse-scanner/docs/DUCKDB_CRASH.md) passes with 2.14M ACKed rows, zero loss/wrong/duplicate bodies; fresh-process DuckDB readers/writers remain active and recover. [S3/catalog partitions](iceberg/multi-system/projects/duckdb-clickhouse/clickhouse-scanner/docs/DUCKDB_DEPENDENCIES.md) pass with 3.38M hot ACKs and unchanged 2M cold source; native-client timeouts left 2k unacknowledged archive rows applied, so no retries. Fresh healthy archive ACK and physical compression/CPU/memory/disk/I/O checks pass. [Spark reader compatibility](iceberg/multi-system/projects/duckdb-clickhouse/clickhouse-scanner/docs/CONNECTOR_COMPACTOR_PROBE.md) passes 3k complete bodies despite directly observed missing v2 headers; writer conformance remains failed. [Full two-million-row concurrent maintenance](iceberg/multi-system/projects/duckdb-clickhouse/clickhouse-scanner/docs/CONNECTOR_CONCURRENT.md) now preserves all 3.46M hot / 2.097M cold ACKs, but availability fails (32/51 cold reads hit memory limits). Retention cleanup remains unrun; earlier-history re-verification rejects legacy ownership labels. The earlier level-5 approval was revoked; experiments are stopped. Never run level 5. [Native memory isolation](iceberg/multi-system/projects/duckdb-clickhouse/clickhouse-scanner/docs/COLD_MEMORY.md) reproduces error 241 without DuckDB/concurrent writers; a 64MiB watermark alone fails once in three attempts, and the subsequent 8,192-row-block diagnostic also fails with one query thread. No validated memory workaround yet.
- [ ] [Connector group 1: two-million-row round trip](iceberg/multi-system/projects/duckdb-clickhouse/clickhouse-scanner/docs/DUCKDB_CLICKHOUSE_ROUNDTRIP.md) is implemented but **fails** on intermittent read-query wrong answers; the generated-row reproducer needs no object storage and has failed with either thread count. Independent native full-body checks pass all three stored copies (6M row copies). Final Make workflows run 228 passing host controls but correctly return nonzero; the gated rerun retains 2M acknowledged hot rows and leaves cold storage uncommitted. The other four groups now have diagnostic evidence, including failed cold-read availability. [Row-level isolation](iceberg/multi-system/projects/duckdb-clickhouse/clickhouse-scanner/docs/CONNECTOR_READ_ISOLATION.md) additionally stores 1,356,530 bad predicate flags beside correct materialized values; root cause remains unassigned. No capacity ranking before correctness passes.
- [ ] Native DuckDB Iceberg follow-up: HTTP timeout/retry settings and v3/pending-write/schema rejections are tested (see rejection controls above), as is a controlled append/conflict/replan. Extend these to continuous append and process/S3/catalog interruption with independent live readers/writers and durable reconciliation. Keep offered load unchanged through recovery; record starvation/rejection and any loss honestly. Committed deletes, partition evolution, safe retention/cleanup and repeated comparable benchmarks remain unrun. The successful earlier live compactor-crash case uses Spark.
- [ ] Iceberg deeper pass: measure normal-path snapshot compression and full startup/exit costs; isolate catalog/S3/network outages, compactor interruptions and archive/TTL recovery; then run comparable sustained cold/warm performance trials. Self-hosted Tigris remains blocked on an available supported server artifact; managed Tigris is outside scope. Use isolated local SeaweedFS without host AWS configuration or resources.
- [x] `clickhouse/iceberg`: single-server catalog writes, retained snapshots, verified export before TTL deletion, compaction and recovery checks.
- [ ] Extend the Iceberg REST catalog coverage to Polaris/Nessie; DuckDB and ClickHouse readers are covered with SeaweedFS.
- [ ] `ducklake-multi-writer`: independent DuckDB writers coordinated through a PostgreSQL DuckLake catalog; deferred at the user's request.
- [ ] `delta-lake-duckdb`: Delta tables read from DuckDB and Spark.
- [ ] Paimon: write a primary-key inventory changelog from Flink to a local SeaweedFS warehouse. Check one supported reader and a changelog consumer across compaction before expanding the reader matrix. [Paimon design](docs/example-plans/storage-and-lakehouse.md#tables-and-catalogs)
- [x] `garage/s3-cluster`: nine nodes, three zones, fsync/quorums, empty-disk replacement and physical replica audits.
- [x] `seaweedfs/s3-cluster`: separated HA master/volume/filer/etcd/S3 roles, three-datacenter `200` replication, fsync, disk replacement and full replica-byte checks.
- [ ] SeaweedFS extensions: erasure coding for warm volumes and `filer.sync` cross-cluster replication.
- [x] `rustfs/s3-cluster`: four-node EC2+2, strict durability, deep heal and independent rebuilt-shard checks.
- [x] [`ceph/s3-cluster`](ceph/s3-cluster/): three guests, nine BlueStore OSDs, three RGWs, host-domain replication, native deep scrubs, guest crash and permanent disk replacement under load, measured emulated benchmarks and operational gotchas.
- [x] `clickhouse/s3-tiered-storage`: replicated MergeTree TTL movement to Garage, hot/cold/cache benchmarks and three separate failure tests.
- [ ] Compare 1–4 MiB blob put/update/delete and a ScyllaDB pointer to a SeaweedFS body. Verify Aerospike's `max-record-size` and the selected ScyllaDB mutation limit before comparing accepted bodies. Treat a large-cell warning separately from a rejected write. [Storage design and upstream limits](docs/example-plans/storage-and-lakehouse.md#object-and-filesystem-storage)

## Low-latency and HPC storage

[Storage and embedded designs](docs/example-plans/storage-and-lakehouse.md) distinguish a process restart, a rank failure and a physical storage failure.

- [x] `zfs-storage-lab`: dedicated mirror, RAIDZ2 and resumable snapshot-replication setups with fio verification and bounded benchmarks.

- [ ] DAOS: exercise KV and file bodies on one emulated Linux engine. Schedule replication/EC rebuild separately on enough engine ranks and fault domains for the chosen object class. Verify the supported storage mode before claiming persistence. [DAOS hardware and emulation](https://docs.daos.io/latest/admin/hardware/)
- [ ] `tidehunter-embedded`: Mysten Labs' WAL-as-storage KV engine (Rust, used by Sui validators); compare write amplification and point reads with RocksDB.
- [ ] CockroachDB with Pebble: compare large-value storage with separation enabled and disabled on a release that supports the switch. Verify blob-file use before attributing a change in compaction I/O to separation.
- [ ] Hazelcast: compare IMap and Near Cache reads through a partition-owner change. The CP subsystem in 5.7 requires Enterprise and explicit CP members for its guarantees. [Edition and configuration](https://docs.hazelcast.com/hazelcast/5.7/cp-subsystem/cp-subsystem)
- [ ] Chronicle Map: compare recovery of a persisted off-heap map with LMDB after a writer crash. Measure latency per operation under the stated sync policy.

## Document

Designs for [replica-set change streams, BSON compatibility and transactional inventory](docs/example-plans/data-services.md#document-databases).

- [ ] `mongodb-replicaset`: replica set and change streams.
- [ ] `ferretdb-single-node`: MongoDB wire protocol on Postgres (DocumentDB extension).
- [ ] Couchbase single-node query baseline and replicated inventory transactions. Measure SQL++ secondary-index visibility separately from KV durability during failover.

## Search

Designs for [product search, log splits, shard recovery and relevance checks](docs/example-plans/data-services.md#search).

- [ ] OpenSearch cluster: refresh visibility, shard relocation and independently checked ESCI catalog results through primary loss.
- [ ] Meilisearch: typo-tolerant catalog search during asynchronous reindex and index swap. Native shard/replica trials require the appropriate self-hosted Enterprise build. [Edition and topology](https://www.meilisearch.com/blog/horizontal-scaling-with-sharding)
- [ ] Apache SolrCloud: tenant routing, replica search visibility at soft/hard commits and shard leader loss during product updates. Verify recovered versions and facet counts.
- [x] [`typesense/product-search`](typesense/product-search/): consolidated single/three-Raft-replica storefront workload and three application-managed customer shards with three replicas each, 100,000 products, typo/facet/filter tests, hybrid ranking, leader-loss reconciliation and measured benchmarks.
- [ ] `quickwit/`: Rust log search on object storage; supported single/Compose/Kubernetes deployments plus a clustered Kafka-to-S3 immutable-log workload, split/index recovery and indexer/searcher loss. Compare matching log queries with Loki / OpenObserve / GreptimeDB; qualify release health and current licensing.
- [x] [`vespa/phased-product-search`](vespa/phased-product-search/): single/three-content-node profiles, 100,000 products, four ranking modes, controller/node failure under load, native metadata/version verification on every physical copy and measured benchmarks.
- [x] [Vespa storage sharding](vespa/phased-product-search/#native-storage-sharding): 3 groups × 3 content nodes plus separate control/query hosts, 900,000 replica-directed gets per phase, content-node failure with 0 acknowledged loss and four-mode benchmarks.
- [x] Manticore [single-node](manticore/single-node/) and [million-document log search versus Elasticsearch](manticore/log-search/). The custom workload follows part of the db-benchmarks methodology.

## Key-value and cache

Designs for [disk-backed carts, cache refill, leases, data grids and coordination](docs/example-plans/data-services.md#key-value-stores-and-coordination).

- [ ] `redis8-single-node`: Redis 8 (AGPL option) with built-in JSON, search and vector sets.
- [ ] `kvrocks-single-node`: Redis protocol on RocksDB.
- [ ] `garnet-docker-compose-cluster`: Microsoft Garnet (.NET) standalone and cluster mode; Tsavorite storage with tiering to disk, checkpoints and AOF.
- [ ] `keydb-active-replica`: multithreaded Redis fork (Snap) with active-active replicas; low priority, last release v6.3.4 (Oct 2023).
- [ ] PikiwiDB (formerly Pika): Redis-compatible RocksDB storage. Compare larger-than-memory hashes, expiry and recovery with Kvrocks.
- [ ] OCache: three-node consistent-hash cache. Exercise RocksDB for small objects and segmented disk storage for large objects. Quantify misses at the default replication factor of one and refill from a local origin. [Storage](https://github.com/tigrisdata/ocache) · [Cluster defaults](https://github.com/tigrisdata/ocache/blob/main/docs/cluster.md)
- [x] Tarantool [single-node](tarantool/single-node/) and [Compose replica set](tarantool/docker-compose-cluster/): memtx and vinyl, WAL, Lua procedures and synchronous-space leader failover.
- [ ] `memcached-extstore`: pure-cache latency baseline; extstore for large values on flash.
- [ ] Compare logical cache reads/writes, batching, expiry, eviction and refill across Valkey, Redis 8, Dragonfly, Garnet, KeyDB, Kvrocks, PikiwiDB, Tarantool, OCache and Memcached. Compare durable writes separately for matching persistence contracts. [Memcached extstore](https://docs.memcached.org/features/flashstorage/)
- [ ] `etcd-docker-compose-cluster`: Raft, watches, leases.
- [ ] Apache ZooKeeper: three-member ensemble, ephemeral sequential lock and application fencing after an expired worker resumes. Compare the protected service's accepted operations with the etcd lease case.
- [ ] `ignite2-docker-compose-cluster`: partitioned inventory reservations, colocated computation, transactions, persistent restart and rebalance under load; compare Hazelcast / PostgreSQL / Tarantool. Include single-node and supported Kubernetes deployment.
- [ ] `ignite3-docker-compose-cluster`: distribution zones, colocated transactions, majority loss and replica rebuild; compare Ignite 2. Research lab only: official October 2026 status has no next Ignite 3 release planned and recommends Ignite 2.x for new production deployments.
- [ ] `infinispan-docker-compose-cluster`: Red Hat Infinispan (Apache 2.0) data grid; distributed caches, RESP endpoint, cross-site replication; same grid comparison.
- [ ] `oxia-docker-compose-cluster`: StreamNative Oxia (Apache 2.0) sharded metadata store, ZooKeeper/etcd alternative; compare watches, sessions and write throughput with etcd.

## Graph and multi-model

Designs for [graph transactions, fraud traversals and storage failure boundaries](docs/example-plans/data-services.md#graph-and-multi-model-databases).

- [x] [Memgraph and Neo4j fraud detection](memgraph/fraud-detection/): seeded graph and streamed transfers, planted rings and query-result hashes compared on both engines.
- [ ] `ladybugdb-embedded`: embedded columnar graph DB, successor to the archived Kùzu.
- [ ] `arcadedb-single-node`: Apache 2.0 multi-model.
- [ ] `surrealdb-single-node`: SurrealDB 3.0 multi-model (document, graph, vector, time-series).
- [ ] Dgraph: race identity upserts, then repeat through a replicated Alpha group's leader loss and verify distinct IDs and edges.
- [ ] FalkorDB: compare the common single-graph fraud workload with Memgraph/Neo4j. The cluster example assigns one graph key per tenant to native hash slots. [Sharding unit](https://docs.falkordb.com/operations/cluster)
- [ ] TigerGraph Community: GSQL deep-link queries over the existing fraud graph on one server. Verify image architecture before running. [Community deployment limit](https://www.tigergraph.com/pricing/)
- [ ] `typedb-single-node`: TypeDB 3 (Rust, MPL 2.0) strongly typed graph with TypeQL and functions; same fraud workload modelled with types and relations.
- [ ] JanusGraph on Cassandra: race external-ID inserts and verify index results against native graph traversals after a replica interruption.

## Embedded and edge SQL

Designs for [SQL conflicts, read levels, edge failover, backup and embedded KV](docs/example-plans/data-services.md#embedded-and-edge-databases).

- [ ] `turso-single-node`: Rust SQLite-compatible engine; inventory transactions, writer conflicts, crash/reopen and compatibility checks against SQLite. Qualify concurrent-write/replication features for the selected release; deferred until the current database batch finishes.
- [ ] `rqlite-docker-compose-cluster`: SQLite replicated over Raft.
- [ ] LiteFS: local leased primary and asynchronous SQLite replicas. Measure the promotion RPO and verify the former primary is fenced.
- [ ] Litestream: back up SQLite to lab-owned S3-compatible storage and restore to a fresh file at a chosen recovery point. Measure the unarchived tail.
- [x] [`lancedb/versioned-product-search`](lancedb/versioned-product-search/): 100,000-product embedded/versioned retrieval, single and three-reader serving profiles, pinned snapshots, unindexed-tail visibility, compaction, isolated writer/reader crashes and measured benchmarks. Shared-volume serving is not replicated database HA.
- [ ] `go-embedded-kv-comparison`: LMDB/libmdbx vs Badger vs Pebble vs bbolt; point reads, writes, range scans, write amplification.

## Wide-column

Pick a [wide-column workload and recovery design](docs/example-plans/relational-and-analytical.md#wide-column-stores).

- [ ] `accumulo-cell-visibility`: labelled cell scans, tablet reassignment and authorization checks during compaction. Verify the exact visible cells for each user. [Upstream authorization model](https://accumulo.apache.org/docs/4.x/security/authorizations)
- [ ] `cassandra-docker-compose-cluster`: baseline to compare with ScyllaDB.
- [ ] `hbase-docker-compose-cluster`: HBase on HDFS with ZooKeeper; region splits, compare with ScyllaDB/Cassandra on wide rows.
- [ ] `kudu-impala`: mutable historical sensor data, scans during updates, tablet-leader loss and replica rebuild; compare ClickHouse / TimescaleDB with mutation semantics stated. Kudu supplies storage; Impala supplies SQL.

## Identity and access

Designs for [OIDC signing-key rotation, permission revocation, policy rollouts and short-lived access](docs/example-plans/data-services.md#identity-and-access).

- [ ] Keycloak, ZITADEL and authentik: compare OIDC login and signing-key rotation on local providers. Exercise SAML assertion validation in a separate supported flow.
- [ ] SpiceDB versus OpenFGA: use equivalent relationship models on local PostgreSQL. Measure grant/revoke visibility against the consistency option each system actually exposes.
- [ ] `opa-vs-cedar`: policy-as-code authorization; same rules in OPA (Rego) and Cedar, called from a Go service.
- [ ] `teleport-single-node`: short-lived certs for SSH, Postgres and k8s access with session recording.

## Observability pipeline

The [per-system assignments](docs/example-plans/streaming-and-observability.md#observability) identify a concrete workload, fault and independent check for each collector, backend and stack.

Plan and source links: [`observability/README.md`](observability/README.md), including
[the requested ClickHouse comparison article](https://clickhouse.com/resources/engineering/top-infrastructure-monitoring-tools-comparison).

- [ ] Self-hosted stack matrix: ClickStack vs Victoria stack vs GreptimeDB vs OpenObserve vs SigNoz vs Grafana (Prometheus baseline; named Loki/Mimir/Tempo scale-out profile). Reuse the shared corpus and independent completeness/recovery oracle.
- [ ] Optional hosted free-tier checks: Honeycomb and Better Stack; Chronosphere only after no-cost access is confirmed. These are not self-hosted OSS editions. **Remind Muzammil to set up a Honeycomb free account before that comparison.**
- [ ] `nagios/`: GPLv2 Nagios Core host/service checks, plugin-based fleet monitoring, detection/recovery and alert accounting under injected failures; exclude commercial XI features.
- [ ] `zabbix/`: agents/proxies, discovery, supported server HA and database recovery; compare the same host/service fault schedule with Nagios Core and the modern stacks.

- [ ] **Planned comparison:** fair GreptimeDB vs Victoria stack
  comparison using [the comparison contract](observability/README.md#greptimedb-vs-victoria-planned-comparison).
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
- [ ] `tempo/`: compare complete trace trees via trace-ID lookup and TraceQL after local queue or object-store outages. Tempo 3 microservices use a Kafka-compatible queue, live-stores and block-builders. Monolithic mode has a separate write path. Match the ingest/query subset when comparing GreptimeDB. [Tempo architecture](https://grafana.com/docs/tempo/latest/introduction/architecture/)

- [ ] `observability/prometheus-loki-tempo`: a Rust or Go application and worker instrumented through OpenTelemetry Collector, Prometheus metrics, Loki logs, Tempo traces and Grafana correlation by trace ID. Use Garage or SeaweedFS for Loki/Tempo object storage; supported Compose/kind cluster deployments, collector/backend crashes and storage outage/recovery. Track missing/duplicate telemetry against an external request oracle, accounting for sampling and scrape semantics. Compare matched metrics/log queries with the GreptimeDB/Victoria examples; multi-signal correlation is a separate capability. Future example, after the current database batch.

- [ ] `otel-collector-clickhouse`: OpenTelemetry Collector into ClickHouse, visualised in Grafana.
- [ ] `vector-pipeline`: Vector (Rust) log shipping into ClickHouse/Quickwit.
- [ ] `clickstack-otel`: ClickStack with its own collector, UI/settings store and ClickHouse data path. Run the separately listed SigNoz stack against the same incident corpus.
- [ ] `akvorado-flows`: NetFlow/sFlow/IPFIX collection into ClickHouse through Kafka.

## Rust embedded database backlog

- [ ] SlateDB: hand ownership of one local object-store prefix between two Rust writers while independent readers refresh. Verify fencing and reconcile durable acknowledgements. [Embedded-storage designs](docs/example-plans/storage-and-lakehouse.md#embedded-engines-and-memory-grids)

- [x] [Initial candidate research](rust/EMBEDDED_DATABASE_SURVEY.md): Fjall 3.1.13,
  redb 5.0.0 and Turso 0.8.4 documentation and selected source paths reviewed;
  sync/transaction, migration and experimental-feature boundaries recorded.
  The survey itself is research; executed Fjall/redb qualification is separate below.
- [x] [`rust/embedded-databases`](rust/embedded-databases/) durable catalog foundation: native Fjall SyncAll/redb Immediate atomic pairs, independent live Rust actors, owner SIGKILL, retained reopen and byte-checked offline fresh-copy restore. The [100k checkpoint](rust/embedded-databases/config/RESULTS_100K.json) verifies 101,550/101,561 cumulative acknowledged pairs with zero loss or wrong/partial bodies; phase-local recovered reads, typed native lock rejection and CPU/memory/compression/disk/byte/IOPS evidence are checked. The [million-pair profile](rust/embedded-databases/config/RESULTS.json) also verifies 1,000,789/1,000,778 ACKed pairs with zero loss/wrong/partial data; one applied redb intent remains unacknowledged. Shared-host functional diagnostics, not a performance winner or SQL replacement.
- [ ] Rust embedded follow-ups: Fjall prefix/range scans during flush/compaction, redb conflicting transactional updates and long-held snapshot space, bounded EIO/full-disk/pressure, format migration, and isolated repeated single/batch/cache comparisons. Add SQLite / RocksDB on matching contracts; no invented clustering.
- [ ] Qdrant extensions: existing single/cluster examples remain covered; add matched-corpus comparisons with Milvus / Weaviate / pgvector plus skew, shard relocation and permanent disk replacement after the current batch.
