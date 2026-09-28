# TODO

Candidate databases and systems to add as examples. Branch names follow the existing
`<system>-single-node`, `<system>-docker-compose-cluster` and `<system>-kubernetes-{operator,helm}` convention.

## Already covered

- **On `main`:** ClickHouse, CockroachDB, YugabyteDB, Elasticsearch, Kafka, NATS, Neo4j, NebulaGraph, ArangoDB, Dragonfly, Garnet/Valkey (Go comparison), Postgres (k8s/vagrant), MySQL and SQLite clients, Airflow.
- **On branches:** Aerospike, FoundationDB, TiDB, TiKV, YDB, OceanBase, RonDB, SingleStore, ScyllaDB, TigerBeetle, CedarDB, DuckDB, Milvus, Qdrant, Weaviate.

## Priority

- [ ] TimescaleDB
- [ ] RisingWave
- [ ] pgvector
- [ ] Citus
- [ ] StarRocks
- [ ] Iceberg + Trino + MinIO
- [ ] MongoDB replica set
- [ ] Valkey cluster
- [ ] Temporal
- [ ] Restate
- [ ] Redpanda
- [ ] SeaweedFS

## Time-series

- [ ] `timescaledb-single-node`: hypertables and continuous aggregates on Postgres.
- [ ] `questdb-single-node`: ILP ingest and `SAMPLE BY` queries.
- [ ] `victoriametrics-docker-compose-cluster`: vminsert/vmselect/vmstorage as Prometheus long-term storage.
- [ ] `influxdb3-core-single-node`: Arrow/Parquet engine with SQL.
- [ ] `greptimedb-single-node`: unified metrics, logs and traces.

## Streaming databases and CDC

- [ ] `risingwave-single-node`: Postgres-compatible incremental materialized views with built-in CDC.
- [ ] `materialize-emulator`: strict-serializable incremental views.
- [ ] `feldera-single-node`: incremental SQL computation (DBSP).
- [ ] `debezium-postgres-kafka`: CDC from Postgres into Kafka, then ClickHouse.

## Message streaming and queues

- [ ] `redpanda-single-node` / `redpanda-docker-compose-cluster`: Kafka API without JVM or ZooKeeper; compare with the Kafka examples.
- [ ] `iggy-single-node`: Apache Iggy, Rust message streaming over TCP/QUIC/HTTP.
- [ ] `fluvio-single-node`: Rust streaming with WASM SmartModules.
- [ ] `pulsar-single-node`: broker/BookKeeper split, tiered storage.
- [ ] `rabbitmq-streams`: classic queues vs streams.

## Durable execution and workflow engines

- [ ] `temporal-docker-compose-cluster`: workflows, activities, retries, signals; Go and Python workers backed by Postgres.
- [ ] `restate-single-node`: Rust durable execution runtime; virtual objects, durable promises, idempotent handlers.
- [ ] `inngest-self-hosted`: self-hosted Inngest server with Postgres + Redis; steps, retries, throttling, fan-out (complements `js/inngest-js` and `python/inngest-py`).
- [ ] `hatchet-single-node`: Postgres-backed task queue and DAG workflows.
- [ ] `dbos-postgres`: durable workflows as a library on top of Postgres.
- [ ] Comparison example: same order/payment saga implemented in Temporal, Restate and Inngest.

## Postgres ecosystem

- [ ] `citus-docker-compose-cluster`: sharded Postgres by distribution column.
- [ ] `pgvector-single-node`: compare with the Milvus, Qdrant and Weaviate examples.
- [ ] `paradedb-single-node`: BM25 full-text search inside Postgres.
- [ ] `pgbouncer-pooler` / `pgdog-pooler`: connection pooling and sharding proxy.
- [ ] `neon-local`: separated storage and compute, branching.
- [ ] `orioledb-single-node`: undo-log storage engine for Postgres.

## Real-time OLAP

- [ ] `starrocks-docker-compose-cluster`: FE/BE, primary-key upserts, joins.
- [ ] `doris-docker-compose-cluster`: side-by-side with StarRocks.
- [ ] `pinot-docker-compose-cluster`: real-time ingest from Kafka, star-tree index.
- [ ] `druid-docker-compose-cluster`: segments, ingestion specs.
- [ ] `databend-single-node`: Rust cloud warehouse on object storage.

## Lakehouse and object storage

- [ ] `iceberg-trino-minio`: Iceberg REST catalog (Polaris or Nessie) + MinIO + Trino; also read from DuckDB and ClickHouse.
- [ ] `delta-lake-duckdb`: Delta tables read from DuckDB and Spark.
- [ ] `garage-docker-compose-cluster`: self-hosted S3-compatible storage.
- [ ] `seaweedfs-docker-compose-cluster`: master/volume/filer with `xyz` replication (`001`/`010`/`100`), S3 gateway, erasure coding for warm volumes, `filer.sync` cross-cluster replication.
- [ ] Comparison example: 1-4 MB blob put/update/delete latency in SeaweedFS vs Aerospike (`write-block-size 8M`) vs ScyllaDB (large-cell threshold, 16 MB mutation cap); plus metadata-in-Scylla + blob-in-SeaweedFS pattern.

## Low-latency and HPC storage

- [ ] `daos-single-node`: DAOS (`daos-stack/daos`) over `ofi+tcp` in MD-on-SSD mode (no RDMA/PMem); `daos_server` + `daos_engine` + `daos_agent`, `dmg` pool setup, containers, `pydaos` KV API, `dfuse` POSIX mount, replication vs EC object classes.
- [ ] `tidehunter-embedded`: Mysten Labs' WAL-as-storage KV engine (Rust, used by Sui validators); compare write amplification and point reads with RocksDB.
- [ ] `cockroachdb-value-separation`: Pebble blob separation (v25.4+) on vs off for large values.

## Document

- [ ] `mongodb-replicaset`: replica set and change streams.
- [ ] `ferretdb-single-node`: MongoDB wire protocol on Postgres (DocumentDB extension).
- [ ] `couchbase-single-node`: KV + N1QL + indexes.

## Search

- [ ] `opensearch-docker-compose-cluster`
- [ ] `meilisearch-single-node`: Rust, typo-tolerant app search.
- [ ] `typesense-single-node`
- [ ] `quickwit-single-node`: Rust log search on object storage.
- [ ] `vespa-single-node`: hybrid search and vector ranking.
- [ ] `manticore-single-node`

## Key-value and cache

- [ ] `valkey-cluster`: Valkey 9 cluster with atomic slot migration.
- [ ] `redis8-single-node`: Redis 8 (AGPL option) with built-in JSON, search and vector sets.
- [ ] `kvrocks-single-node`: Redis protocol on RocksDB.
- [ ] `etcd-docker-compose-cluster`: Raft, watches, leases.

## Graph and multi-model

- [ ] `memgraph-single-node`: in-memory Cypher; compare with Neo4j.
- [ ] `ladybugdb-embedded`: embedded columnar graph DB, successor to the archived Kùzu.
- [ ] `arcadedb-single-node`: Apache 2.0 multi-model.
- [ ] `surrealdb-single-node`: SurrealDB 3.0 multi-model (document, graph, vector, time-series).
- [ ] `dgraph-single-node`
- [ ] `janusgraph-cassandra`

## Embedded and edge SQL

- [ ] `turso-single-node`: SQLite rewrite in Rust with MVCC `BEGIN CONCURRENT`.
- [ ] `rqlite-docker-compose-cluster`: SQLite replicated over Raft.
- [ ] `litefs-litestream`: SQLite replication and backup to S3.
- [ ] `lancedb-embedded`: embedded vector DB on Lance format.

## Wide-column and sharding

- [ ] `cassandra-docker-compose-cluster`: baseline to compare with ScyllaDB.
- [ ] `vitess-docker-compose-cluster`: sharded MySQL with VSchema and resharding.

## Observability pipeline

- [ ] `otel-collector-clickhouse`: OpenTelemetry Collector into ClickHouse, visualised in Grafana.
- [ ] `vector-pipeline`: Vector (Rust) log shipping into ClickHouse/Quickwit.
