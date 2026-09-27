# ClickHouse - Replication using ClickHouse Keeper and Cluster Discovery

An 11 node ClickHouse setup where the `cluster_hits` cluster has **no static host list**. Each data node
registers itself in ClickHouse Keeper under `/clickhouse/discovery/cluster_hits` and learns the other members
from there ([cluster discovery](https://clickhouse.com/docs/operations/cluster-discovery), still experimental).
Adding a replica is just starting another server with the same `clusters.xml` and a `SHARD`.

| Nodes | Role |
|-------|------|
| `clickhouse-server-01..03` | keeper + data, shards `001`..`003` |
| `clickhouse-server-04..09` | data, two more replicas of each shard |
| `clickhouse-server-10..11` | keeper + discovery *observer* (sees the cluster, never joins it) |
| `clickhouse-server-12` | data, shard `001`, only started by `make scale-out` |

The five keepers form one RAFT ensemble (`configs/clickhouse/keeper_server.xml`). Every server exposes Prometheus
metrics on port `8001`; Prometheus scrapes them and Grafana starts with a provisioned datasource and the
**ClickHouse Cluster** dashboard (`configs/grafana/dashboards/clickhouse-cluster.json`) as its home page.

## Testing

```bash
make up         # start and wait for every server to be healthy
make test       # scripts/test.sh: discovered members, ON CLUSTER schema, inserts on two shards, read back
make status     # system.clusters + the registrations in keeper (sql/0-discovery.sql)
make scale-out  # start clickhouse-server-12; it joins shard 001 and fetches the existing parts
make cli        # clickhouse-client on clickhouse-server-01
make down       # remove containers and volumes
```

- Grafana: http://localhost:13000 (anonymous admin) — override with `GRAFANA_PORT`
- Prometheus: http://localhost:19090 — override with `PROMETHEUS_PORT`
- ClickHouse HTTP (server-01): http://localhost:18123 — override with `CLICKHOUSE_HTTP_PORT`

SQL files under `sql/` are mounted at `/ch-replica-sql` in every container, e.g.
`docker compose exec clickhouse-server-01 bash -c 'clickhouse-client < /ch-replica-sql/4-results.sql'`.

- `0-discovery.sql` — members of `cluster_hits` and their registrations in keeper
- `1-schema-base-tables.sql`, `2-schema-distributed-tables.sql` — `ON CLUSTER cluster_hits` DDL
- `3-insert.sql` — 500 random rows into the local table of the server it runs on
- `4-results.sql` — reads through the Distributed tables
- `5-keeper.sql` — replication state in keeper

The unreplicated SummingMergeTree only has data on the server that received the insert, not on its
replicas, since Materialized Views are triggered on insert and NOT on replication.

Notes:

- Each server registers its `hostname`, so hostnames must resolve from the other containers; here they
  equal the compose service names.
- `ON CLUSTER` DDL is not replayed for servers that join later; re-apply the schema on them (`make scale-out`
  does), or use a `Replicated` database engine to sync schema automatically.
- `CLICKHOUSE_SKIP_USER_SETUP=1` keeps the passwordless `default` user reachable from other nodes. Local demo only.
