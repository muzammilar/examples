# examples

Initial experiments before 2026, including early ClickHouse setups, were done manually; later experiments use Claude, Codex and other AI tools, since the goal is to benchmark systems, identify their failure and recovery scenarios, and uncover potential bugs in the applications.

## Language Examples

These directories were merged from standalone repos (now archived) using `git subtree`, with full history preserved.

| Directory | Former repo |
|---|---|
| [`cpp/`](cpp) | [muzammilar/examples-cpp](https://github.com/muzammilar/examples-cpp) |
| [`erlang/`](erlang) | — |
| [`go/`](go) | [muzammilar/examples-go](https://github.com/muzammilar/examples-go) |
| [`js/`](js) | [muzammilar/examples-js](https://github.com/muzammilar/examples-js) |
| [`python/`](python) | [muzammilar/examples-python](https://github.com/muzammilar/examples-python) |
| [`rust/`](rust) | [muzammilar/examples-rust](https://github.com/muzammilar/examples-rust) |

Some Go modules still declare `github.com/muzammilar/examples-go/...` module paths; they build fine from their new location.

## Systems

Databases, pub/sub, streaming and infrastructure examples, one folder per system.

New systems cover supported single-node, Docker Compose cluster and Kubernetes
operator deployments, plus a representative workload **IN A CLUSTER WHERE POSSIBLE**.
Workload examples compare relevant competitors using the same data and correctness
checks; measurements and unsupported deployment modes belong in each README.

Every experiment measures CPU utilization, memory, data compression and disk usage,
alongside correctness, throughput, latency and recovery. Include whole-stack costs,
I/O/network and measurement gaps; see the [test concepts](AGENTS.md#evaluation-stages-and-backend-contract)
and [measurement requirements](AGENTS.md#mandatory-measurements-for-every-experiment).

To add or test a system, follow [`FORMAT.md`](FORMAT.md): folder layout, Make targets, Compose and kind conventions, scaling, failover, benchmarks and README format.

| System | Folder | Category | Website | GitHub |
|--------|--------|----------|---------|--------|
| Aerospike | [`aerospike/`](aerospike) | Key-value | [aerospike.com](https://aerospike.com/) | [aerospike/aerospike-server](https://github.com/aerospike/aerospike-server) |
| Airflow | [`airflow/`](airflow) | Workflow orchestration | [airflow.apache.org](https://airflow.apache.org/) | [apache/airflow](https://github.com/apache/airflow) |
| Ansible | [`ansible/`](ansible) | Configuration management | [ansible.com](https://www.ansible.com/) | [ansible/ansible](https://github.com/ansible/ansible) |
| ArangoDB | [`arrangodb/`](arrangodb) | Multi-model (document + graph) | [arango.ai](https://arango.ai/) | [arangodb/arangodb](https://github.com/arangodb/arangodb) |
| CedarDB | [`cedardb/`](cedardb) | HTAP / relational | [cedardb.com](https://cedardb.com/) | — |
| Ceph | [`ceph/`](ceph) | Object / block / filesystem | [ceph.io](https://ceph.io/) | [ceph/ceph](https://github.com/ceph/ceph) |
| ClickHouse | [`clickhouse/`](clickhouse) | OLAP / analytics | [clickhouse.com](https://clickhouse.com/) | [ClickHouse/ClickHouse](https://github.com/ClickHouse/ClickHouse) |
| CockroachDB | [`cockroachdb/`](cockroachdb) | Distributed SQL | [cockroachlabs.com](https://www.cockroachlabs.com/) | [cockroachdb/cockroach](https://github.com/cockroachdb/cockroach) |
| Dragonfly | [`dragonfly/`](dragonfly) | Cache / in-memory | [dragonflydb.io](https://www.dragonflydb.io/) | [dragonflydb/dragonfly](https://github.com/dragonflydb/dragonfly) |
| DuckDB | [`duckdb/`](duckdb) | Embedded analytics | [duckdb.org](https://duckdb.org/) | [duckdb/duckdb](https://github.com/duckdb/duckdb) |
| Elasticsearch | [`elasticsearch/`](elasticsearch) | Search | [elastic.co](https://www.elastic.co/elasticsearch) | [elastic/elasticsearch](https://github.com/elastic/elasticsearch) |
| FoundationDB | [`foundationdb/`](foundationdb) | Key-value | [foundationdb.org](https://www.foundationdb.org/) | [apple/foundationdb](https://github.com/apple/foundationdb) |
| Garage | [`garage/`](garage) | S3 object storage | [garagehq.deuxfleurs.fr](https://garagehq.deuxfleurs.fr/) | [deuxfleurs-org/garage](https://github.com/deuxfleurs-org/garage) |
| GreptimeDB | [`greptimedb/`](greptimedb) | Metrics / logs / observability | [greptime.com](https://greptime.com/) | [GreptimeTeam/greptimedb](https://github.com/GreptimeTeam/greptimedb) |
| Iceberg | [`iceberg/`](iceberg) | Lakehouse tables | [iceberg.apache.org](https://iceberg.apache.org/) | [apache/iceberg](https://github.com/apache/iceberg) |
| InfluxDB | [`influxdb/`](influxdb) | Time-series | [influxdata.com](https://www.influxdata.com/products/influxdb/) | [influxdata/influxdb](https://github.com/influxdata/influxdb) |
| Kafka | [`kafka/`](kafka) | Streaming | [kafka.apache.org](https://kafka.apache.org/) | [apache/kafka](https://github.com/apache/kafka) |
| kdb+ | [`kdb/`](kdb) | Time-series | [kx.com](https://kx.com/) | — |
| Kubernetes | [`k8s/`](k8s) | Container orchestration | [kubernetes.io](https://kubernetes.io/) | [kubernetes/kubernetes](https://github.com/kubernetes/kubernetes) |
| LanceDB | [`lancedb/`](lancedb) | Embedded vector / hybrid retrieval | [lancedb.com](https://lancedb.com/) | [lancedb/lancedb](https://github.com/lancedb/lancedb) |
| Manticore Search | [`manticore/`](manticore) | Search | [manticoresearch.com](https://manticoresearch.com/) | [manticoresoftware/manticoresearch](https://github.com/manticoresoftware/manticoresearch) |
| Memgraph | [`memgraph/`](memgraph) | Graph (in-memory) | [memgraph.com](https://memgraph.com/) | [memgraph/memgraph](https://github.com/memgraph/memgraph) |
| Milvus | [`milvus/`](milvus) | Vector | [milvus.io](https://milvus.io/) | [milvus-io/milvus](https://github.com/milvus-io/milvus) |
| MySQL | [`mysql/`](mysql) | Relational / distributed SQL | [mysql.com](https://www.mysql.com/) | [mysql/mysql-server](https://github.com/mysql/mysql-server) |
| NATS | [`nats.io/`](nats.io) | Core NATS: pub/sub; JetStream: streaming / KV | [nats.io](https://nats.io/) | [nats-io/nats-server](https://github.com/nats-io/nats-server) |
| NebulaGraph | [`nebula-graph/`](nebula-graph) | Graph | [nebula-graph.io](https://www.nebula-graph.io/) | [vesoft-inc/nebula](https://github.com/vesoft-inc/nebula) |
| Neo4j | [`neo4j/`](neo4j) | Graph | [neo4j.com](https://neo4j.com/) | [neo4j/neo4j](https://github.com/neo4j/neo4j) |
| OceanBase | [`oceanbase/`](oceanbase) | Distributed SQL | [oceanbase.com](https://en.oceanbase.com/) | [oceanbase/oceanbase](https://github.com/oceanbase/oceanbase) |
| PostgreSQL | [`postgres/`](postgres) | Relational / sharding | [postgresql.org](https://www.postgresql.org/) | [postgres/postgres](https://github.com/postgres/postgres) |
| Qdrant | [`qdrant/`](qdrant) | Vector | [qdrant.tech](https://qdrant.tech/) | [qdrant/qdrant](https://github.com/qdrant/qdrant) |
| QEMU | [`qemu/`](qemu) | VM / virtualization | [qemu.org](https://www.qemu.org/) | [qemu/qemu](https://github.com/qemu/qemu) |
| QuestDB | [`questdb/`](questdb) | Time-series | [questdb.com](https://questdb.com/) | [questdb/questdb](https://github.com/questdb/questdb) |
| Redpanda | [`redpanda/`](redpanda) | Streaming (Kafka API) | [redpanda.com](https://www.redpanda.com/) | [redpanda-data/redpanda](https://github.com/redpanda-data/redpanda) |
| RisingWave | [`risingwave/`](risingwave) | Streaming SQL | [risingwave.com](https://risingwave.com/) | [risingwavelabs/risingwave](https://github.com/risingwavelabs/risingwave) |
| RonDB | [`rondb/`](rondb) | In-memory SQL / key-value | [rondb.com](https://www.rondb.com/) | [logicalclocks/rondb](https://github.com/logicalclocks/rondb) |
| RustFS | [`rustfs/`](rustfs) | S3 object storage | [rustfs.com](https://rustfs.com/) | [rustfs/rustfs](https://github.com/rustfs/rustfs) |
| ScyllaDB | [`scylladb/`](scylladb) | Wide-column | [scylladb.com](https://www.scylladb.com/) | [scylladb/scylladb](https://github.com/scylladb/scylladb) |
| SeaweedFS | [`seaweedfs/`](seaweedfs) | Distributed filesystem / S3 | [seaweedfs.com](https://seaweedfs.com/) | [seaweedfs/seaweedfs](https://github.com/seaweedfs/seaweedfs) |
| SingleStore | [`singlestore/`](singlestore) | Distributed SQL | [singlestore.com](https://www.singlestore.com/) | — |
| Skytable | [`skytable/`](skytable) | Key-value | [skytable.io](https://skytable.io/) | [skytable/skytable](https://github.com/skytable/skytable) |
| StarRocks | [`starrocks/`](starrocks) | Mutable analytics | [starrocks.io](https://www.starrocks.io/) | [StarRocks/starrocks](https://github.com/StarRocks/starrocks) |
| Tarantool | [`tarantool/`](tarantool) | In-memory DB + Lua | [tarantool.io](https://www.tarantool.io/) | [tarantool/tarantool](https://github.com/tarantool/tarantool) |
| TiDB | [`tidb/`](tidb) | Distributed SQL | [pingcap.com](https://www.pingcap.com/tidb/) | [pingcap/tidb](https://github.com/pingcap/tidb) |
| Tigris (planned) | [`tigris/`](tigris) | Managed S3 resilience / object-storage comparison | [tigrisdata.com](https://www.tigrisdata.com/) | [SDKs](https://github.com/tigrisdata/storage) |
| TigerBeetle | [`tigerbeetle/`](tigerbeetle) | Ledger / OLTP | [tigerbeetle.com](https://tigerbeetle.com/) | [tigerbeetle/tigerbeetle](https://github.com/tigerbeetle/tigerbeetle) |
| TiKV | [`tikv/`](tikv) | Key-value | [tikv.org](https://tikv.org/) | [tikv/tikv](https://github.com/tikv/tikv) |
| TimescaleDB | [`timescaledb/`](timescaledb) | Time-series | [tigerdata.com](https://www.tigerdata.com/timescaledb) | [timescale/timescaledb](https://github.com/timescale/timescaledb) |
| Typesense | [`typesense/`](typesense) | Faceted / hybrid search | [typesense.org](https://typesense.org/) | [typesense/typesense](https://github.com/typesense/typesense) |
| Vagrant | [`vagrant/`](vagrant) | VM / virtualization | [vagrantup.com](https://www.vagrantup.com/) | [hashicorp/vagrant](https://github.com/hashicorp/vagrant) |
| Valkey | [`valkey/`](valkey) | Cache / in-memory | [valkey.io](https://valkey.io/) | [valkey-io/valkey](https://github.com/valkey-io/valkey) |
| Vespa | [`vespa/`](vespa) | Distributed retrieval / ranking | [vespa.ai](https://vespa.ai/) | [vespa-engine/vespa](https://github.com/vespa-engine/vespa) |
| Weaviate | [`weaviate/`](weaviate) | Vector | [weaviate.io](https://weaviate.io/) | [weaviate/weaviate](https://github.com/weaviate/weaviate) |
| YDB | [`ydb/`](ydb) | Distributed SQL | [ydb.tech](https://ydb.tech/) | [ydb-platform/ydb](https://github.com/ydb-platform/ydb) |
| YugabyteDB | [`yugabyte/`](yugabyte) | Distributed SQL (YSQL + YCQL) | [yugabyte.com](https://www.yugabyte.com/) | [yugabyte/yugabyte-db](https://github.com/yugabyte/yugabyte-db) |
| ZFS | [`zfs/`](zfs) | Filesystem / volume manager | [openzfs.org](https://openzfs.org/) | [openzfs/zfs](https://github.com/openzfs/zfs) |

See [S3 object-storage comparisons](object-storage/) for clustered S3 workloads,
disk-replacement tests, acknowledged-loss accounting and benchmark limitations.

## Analytical database follow-ups

**High priority, next after the current work is complete:**

- **CedarDB** — [existing single-node example and measurements](cedardb/README.md); keep follow-up work separate from the distributed-system backlog.
- **RayforceDB vs DuckDB** — [initial Python/C++/Rust comparison](rayforce/duckdb-comparison/README.md) measures bulk CSV loading, analytical queries, CPU/memory time series and block-I/O/input-storage accounting at equal 1/4-CPU budgets. The [single/batch append comparison](rayforce/duckdb-comparison/README.md#ingestion-comparison) adds 144 measured Python/C++/Rust trials with full-body verification and resource accounting. Follow up with the columnar Appender, prepared SQL, genuinely cold persisted queries, native disk storage/compression and persistence/recovery paths; document semantic differences and unsupported cases.

## Planned distributed database workloads

These are documentation backlog entries, except Qdrant which already has examples.
Proposed extensions have not been run. See
[`TODO.md`](TODO.md) for the broader backlog.

| System | Potential cluster example | Comparison |
|---|---|---|
| [Apache Pinot](https://docs.pinot.apache.org/) | Kafka-fed delivery status, ingestion upserts, out-of-order events, segment recovery and server failure | StarRocks / ClickHouse on identical event history |
| [Apache Doris](https://doris.apache.org/docs/4.x/features-architecture/system-architecture/) | Mutable orders/inventory joins; replicated tablets versus disaggregated storage, compute isolation and disk recovery | StarRocks / ClickHouse |
| [Apache Kudu](https://kudu.apache.org/docs/index.html) | Update historical sensor readings while scanning; tablet-leader failure and replica rebuild, SQL through Impala | ClickHouse / TimescaleDB, with mutation semantics stated |
| [Apache Ignite 2](https://ignite.apache.org/download/) | Partitioned inventory reservations, colocated compute, transactional conflicts, rebalance and persistence recovery | Hazelcast / PostgreSQL; in-memory and durable modes separately |
| [Apache Ignite 3](https://ignite.apache.org/docs/ignite3/latest/) | Distribution zones, colocated transactions, majority loss and replica rebuild; compare with Ignite 2 | Ignite 2 / PostgreSQL; research lab, maintenance caveat below |
| [CrateDB](https://docs.cratedb.com/projects/crate-reference/en/latest/general/ddl/sharding.html) | Industrial telemetry and text filters, tenant skew, three shards with three copies, shard relocation and disk loss | GreptimeDB / TimescaleDB / Elasticsearch on matching query subsets |
| [Qdrant](qdrant/) (existing examples) | Extend clustered filtered/hybrid product retrieval with skew, shard movement and permanent disk replacement | Milvus / Weaviate / pgvector on the same corpus; existing tests are not new results |
| [SpacetimeDB](https://github.com/clockworklabs/SpacetimeDB) | Multiplayer world state, transactional Rust modules, subscriptions, reconnect/replay and server restart | PostgreSQL + application/WebSocket service; document the selected release's real replication support |

Ignite's official October 2026 status says no next Ignite 3 release is planned and
recommends maintained Ignite 2.x for new production deployments. Keep both in the
research backlog and qualify them independently; see the [upstream status](https://ignite.apache.org/download/).

## Planned embedded database workloads

| System | Potential example | Comparison |
|---|---|---|
| [Turso Database](https://github.com/tursodatabase/turso) | Embedded Rust SQL: concurrent inventory updates, transaction conflicts, crash/reopen and SQLite compatibility | SQLite / PostgreSQL; qualify preview features separately |
| [Fjall](https://github.com/fjall-rs/fjall) — future `rust/embedded-databases` project | In-process LSM ingestion, scans during compaction, atomic batches and kill/reopen recovery | redb / RocksDB with matching sync/ACK rules |
| [redb](https://github.com/cberner/redb) — same Rust project | Embedded transactional catalog, snapshot readers during writes and crash/reopen recovery | Fjall / SQLite with matching transaction boundaries |

## Planned observability workloads

Future separate system examples: **Prometheus**, **Loki** and **Tempo**, each with
supported deployment shapes and a representative workload in a cluster where
possible. Compare matching metrics/logs/tracing capabilities with GreptimeDB;
keep sampling, durability and unsupported query differences explicit.

Future integrated observability example: **Prometheus + Loki + Tempo + Grafana**, with an
OpenTelemetry-instrumented application, trace-linked metrics/logs, S3-backed
Loki/Tempo storage and collector/backend recovery tests. Compare matching queries
with the telemetry database examples while accounting for sampling and scrape
semantics. This is a [backlog item](TODO.md#observability-pipeline), not implemented
in the current database batch.

## Observability

See [`observability/README.md`](observability/README.md) for the planned comparison:
**ClickStack vs Victoria stack vs GreptimeDB vs OpenObserve vs SigNoz vs Grafana
(Loki/Mimir/Tempo, with a Prometheus baseline)**, optional free hosted checks for
Honeycomb / Better Stack / Chronosphere where available, and Nagios Core / Zabbix
infrastructure monitoring. It includes separate system examples, potential
workloads, shared correctness/recovery rules and the Honeycomb account reminder.

Starting reference: [ClickHouse infrastructure monitoring tools comparison](https://clickhouse.com/resources/engineering/top-infrastructure-monitoring-tools-comparison).
This is future work, not a set of benchmark results.

## External Projects (Git Submodules)

See [git submodule examples](go/ext/README.md).

Add a submodule:

```sh
git submodule add https://github.com/muzammilar/<repo>.git <repo>
```
