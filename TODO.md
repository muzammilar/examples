# TODO

Candidate databases and systems to add as examples. Branch names follow the existing
`<system>-single-node`, `<system>-docker-compose-cluster` and `<system>-kubernetes-{operator,helm}` convention.

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

- [ ] Postgres scale-out: Citus, PgDog, Multigres, Neon
- [ ] MySQL sharding: Vitess, ShardingSphere, PolarDB-X, NDB Cluster
- [x] pgvector
- [ ] StarRocks
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
- [ ] `greptimedb-single-node`: unified metrics, logs and traces.
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
- [ ] `neon-docker-compose-cluster`: Neon (Apache 2.0) storage/compute split from the upstream `docker-compose/` setup; compute + safekeepers (Paxos WAL quorum) + pageserver + storage broker, layers offloaded to MinIO. Copy-on-write branching, point-in-time branches, read replicas sharing storage, compute restart without data loss; `failover` kills a safekeeper.
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
- [ ] `spacetimedb-single-node`: SpacetimeDB (BSL) in-memory relational DB with WASM modules (Rust/C#) as stored procedures and client subscriptions; multiplayer-game state workload.

## Real-time OLAP

- [ ] `starrocks-docker-compose-cluster`: FE/BE, primary-key upserts, joins.
- [ ] `doris-docker-compose-cluster`: side-by-side with StarRocks.
- [ ] `pinot-docker-compose-cluster`: real-time ingest from Kafka, star-tree index.
- [ ] `druid-docker-compose-cluster`: segments, ingestion specs.
- [ ] `databend-single-node`: Rust cloud warehouse on object storage.
- [ ] `firebolt-core-single-node`: free self-hosted Firebolt engine; compare with ClickHouse and StarRocks.
- [ ] `exasol-personal`: free single-user in-memory MPP; Docker image is x86-only and capped at 10 GiB.
- [ ] `vertica-community`: free Community Edition (3 nodes, 1 TB); MPP column store with projections; compare with ClickHouse and StarRocks.
- [ ] `cratedb-docker-compose-cluster`: CrateDB (Apache 2.0) distributed SQL on Lucene; time-series and full-text in one table, shard allocation and `failover`.
- [ ] `heavydb-single-node`: HeavyDB (formerly OmniSciDB/MapD, Apache 2.0) GPU-accelerated SQL; CPU mode on a Mac, GPU numbers only with an NVIDIA box.

## Embedded analytics

- [x] `duckdb-zfs-recovery`: four Quack clients, real ZFS snapshot replication, interrupted receive/resume, read-only clones and fenced-primary promotion with measured asynchronous loss.

- [ ] `hyper-api-embedded`: Salesforce/Tableau Hyper (Umbra lineage, compiled queries) via `pip install tableauhyperapi`; free, closed source. macOS arm64 wheels stop after 0.0.26479 (newer releases: Linux x86_64 and Windows only), so pin that on a Mac. Compare with DuckDB on the same data.
- [ ] `polars-datafusion`: Polars and DataFusion on the same Parquet files as the DuckDB example.
- [ ] `monetdb-single-node`: MonetDB (MPL 2.0), the original column store (vectorized execution lineage of DuckDB); same queries as the DuckDB example.

## Lakehouse and object storage

- [ ] `hoglake-local`: [PostHog Hoglake](https://github.com/PostHog/hoglake), a Postgres-native lakehouse catalog; investigate its native Trino connector and catalog recovery. Documentation backlog only; no implementation in the current Iceberg + Trino + MinIO work.
- [x] `iceberg-trino-minio`: persistent Iceberg REST fixture + 4 MinIO nodes + Trino 484 coordinator/3 workers; million-row reconciliation, worker task retry, storage-node failure and catalog restart.
- [x] `iceberg/multi-system`: five SeaweedFS-backed DuckDB/ClickHouse data paths, Spark compaction, safe archive handoffs and a three-shard/three-copy native serving cache with replica recovery.
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
- [ ] `quickwit-single-node`: Rust log search on object storage.
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
- [ ] `ignite-docker-compose-cluster`: Apache Ignite 3 in-memory distributed SQL + KV with Raft-replicated partitions and colocated compute; compare with Hazelcast and Tarantool.
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

- [ ] `turso-single-node`: SQLite rewrite in Rust with MVCC `BEGIN CONCURRENT`.
- [ ] `rqlite-docker-compose-cluster`: SQLite replicated over Raft.
- [ ] `litefs-litestream`: SQLite replication and backup to S3.
- [x] [`lancedb/versioned-product-search`](lancedb/versioned-product-search/): 100,000-product embedded/versioned retrieval, single and three-reader serving profiles, pinned snapshots, unindexed-tail visibility, compaction, isolated writer/reader crashes and measured benchmarks. Shared-volume serving is not replicated database HA.
- [ ] `go-embedded-kv-comparison`: LMDB/libmdbx vs Badger vs Pebble vs bbolt; point reads, writes, range scans, write amplification.

## Wide-column

- [ ] `cassandra-docker-compose-cluster`: baseline to compare with ScyllaDB.
- [ ] `hbase-docker-compose-cluster`: HBase on HDFS with ZooKeeper; region splits, compare with ScyllaDB/Cassandra on wide rows.
- [ ] `kudu-impala`: Apache Kudu columnar storage with fast upserts, queried from Impala or Trino.

## Identity and access

- [ ] `keycloak-oidc`: Keycloak realm with OIDC/SAML login; Go and Python apps validating tokens (compare Zitadel/Authentik).
- [ ] `spicedb-vs-openfga`: Zanzibar-style relationship authorization; same schema and checks in SpiceDB and OpenFGA backed by Postgres.
- [ ] `opa-vs-cedar`: policy-as-code authorization; same rules in OPA (Rego) and Cedar, called from a Go service.
- [ ] `teleport-single-node`: short-lived certs for SSH, Postgres and k8s access with session recording.

## Observability pipeline

- [ ] `otel-collector-clickhouse`: OpenTelemetry Collector into ClickHouse, visualised in Grafana.
- [ ] `vector-pipeline`: Vector (Rust) log shipping into ClickHouse/Quickwit.
- [ ] `clickstack-otel`: ClickStack (HyperDX) or SigNoz; logs, traces and metrics in ClickHouse via OpenTelemetry.
- [ ] `akvorado-flows`: NetFlow/sFlow/IPFIX collection into ClickHouse through Kafka.
