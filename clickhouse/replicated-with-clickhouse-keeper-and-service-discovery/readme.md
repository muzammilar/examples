# ClickHouse - Replication using ClickHouse Keeper and Cluster Discovery

An 11 node ClickHouse setup where no config file lists hosts. Every data node registers itself in ClickHouse
Keeper and learns the other members from there
([cluster discovery](https://clickhouse.com/docs/operations/cluster-discovery), still experimental), and the
schema lives in [`Replicated` databases](https://clickhouse.com/docs/engines/database-engines/replicated), so a
node started later gets the tables and the data of its shard without anyone editing config or running DDL.

| Nodes | Role |
|-------|------|
| `clickhouse-server-01..03` | keeper + data, shards `001`..`003` |
| `clickhouse-server-04..09` | data, two more replicas of each shard |
| `clickhouse-server-10..11` | keeper + discovery *observer*: sees every cluster, never joins one |
| added with `make add-node` | data, any name, any shard (including a new one) |

## Testing

```bash
make up                                               # start and wait for every server to be healthy
make test                                             # members, schema, inserts on two shards, read back
make status                                           # system.clusters + registrations in keeper (sql/0-discovery.sql)

make add-node                                         # add clickhouse-server-12 to shard 001 (defaults)
make add-node NAME=clickhouse-server-13 SHARD=002     # any name / shard; a new shard like 004 works too
make nodes                                            # nodes added with add-node
make remove-node NAME=clickhouse-server-13            # stop it and clean its replica metadata out of keeper

make cli                                              # clickhouse-client on clickhouse-server-01
make down                                             # remove all containers (added nodes too) and volumes
```

- Grafana: http://localhost:13000 (anonymous admin), override with `GRAFANA_PORT`
- Prometheus: http://localhost:19090, override with `PROMETHEUS_PORT`
- ClickHouse HTTP (server-01): http://localhost:18123, override with `CLICKHOUSE_HTTP_PORT`

## How it works

### Discovery (`configs/clickhouse/clusters*.xml`)

Data nodes register in two clusters; a cluster is named after the last segment of its Keeper path:

- `cluster_hits` (`/clickhouse/discovery/cluster_hits`), where `<shard from_env="SHARD"/>` picks the shard. The Distributed
  tables use it, so they always query the current members.
- `cluster_all_nodes` (`/clickhouse/discovery/cluster_all_nodes`) has no `<shard>`, and discovery puts every node into one
  shard as replicas of each other. `clusterAllReplicas('cluster_all_nodes', ...)` or `ON CLUSTER cluster_all_nodes`
  reaches every data node regardless of its shard.

Registrations are ephemeral Keeper nodes: a server that stops closes its session and drops out of both
clusters (after the session timeout if it crashes). Nodes 10 and 11 use `<multicluster_root_path>` with `<observer/>`:
they find every cluster under `/clickhouse/discovery` without registering themselves. Distributed queries between
nodes authenticate with a shared `<secret>`.

Each server registers its hostname, so hostnames must resolve from the other containers. Here they equal the
container names.

### Schema (`sql/`)

Discovery only changes cluster membership, not databases or tables. `test` and `test_mvs` are `Replicated`
databases, `Replicated('/clickhouse/databases/<db>', '{shard}', '{replica}')`. DDL run on any one node is
written to Keeper and applied on every replica of the database on every shard, and a node that creates the
database later replays all of it. That's why `ON CLUSTER` isn't used, and it isn't allowed inside a `Replicated`
database.

- `0-discovery.sql`: members of the discovered clusters and their registrations in keeper
- `1-databases.sql`: the `Replicated` databases; run on every data node (`scripts/schema.sh`, `scripts/add-node.sh`)
- `2-schema-base-tables.sql`, `3-schema-distributed-tables.sql`: tables, run once on any node
- `4-insert.sql`: 500 random rows into the local table of the server it runs on
- `5-results.sql`: reads through the Distributed tables
- `6-keeper.sql`: replication state in keeper

`sql/` is mounted at `/ch-replica-sql` in every container, e.g.
`docker exec clickhouse-server-01 bash -c 'clickhouse-client < /ch-replica-sql/5-results.sql'`.

### Adding a node (`scripts/add-node.sh`)

1. `docker compose run` starts a container from the `clickhouse-node` template service with `NODE_NAME` as its
   hostname and `SHARD`; it registers itself and shows up in `cluster_hits` within seconds.
2. `1-databases.sql` runs on it. The `Replicated` databases replay the schema from Keeper (`SYSTEM SYNC DATABASE REPLICA`).
3. Its `ReplicatedMergeTree` tables fetch the shard's existing parts from the other replicas (`SYSTEM SYNC REPLICA`).

If a node with the same name went away without `remove-node`, its old replica metadata is still in Keeper and
`CREATE DATABASE` would fail with `REPLICA_ALREADY_EXISTS`. The script clears it first, since the new node's disk
is empty.

A node in a new shard (for example `SHARD=004`) joins `cluster_hits` as an extra, empty shard. Existing data is
not rebalanced, and only new inserts land there.

### Removing a node (`scripts/remove-node.sh`)

1. `docker stop` closes its Keeper session, so it disappears from `cluster_hits` and the Distributed tables stop
   using it.
2. It then runs `SYSTEM DROP REPLICA '<name>'` and `SYSTEM DROP DATABASE REPLICA '<name>' FROM SHARD '<shard>'`
   from a peer in the same shard. Without this step, the other replicas keep the dead replica's entries in Keeper,
   and the replication log is kept around for a replica that will never return.

The script refuses to remove the last replica of a shard, because its data would be lost. It also refuses the
keeper nodes (01..03, 10, 11), because removing one would shrink the RAFT ensemble in
`configs/clickhouse/keeper_server.xml`, which is static.

## Monitoring

Every server exposes Prometheus metrics on port `8001`. Prometheus finds the ClickHouse containers of this
compose project through the Docker API (`docker_sd_configs`, which is why the socket is mounted), so nodes added
with `make add-node` are scraped without config changes. Grafana starts with provisioned Prometheus and ClickHouse
datasources and the **ClickHouse Cluster** dashboard (`configs/grafana/dashboards/clickhouse-cluster.json`) as its
home page:

- **Discovery**: members per discovered cluster and the live `cluster_hits` membership (queried from observer
  `clickhouse-server-10`), plus replica health on every data node (`clusterAllReplicas`) and servers scraped over time.
  Run `make add-node` / `make remove-node` and watch rows appear and disappear.
- **Cluster, Queries, Replication & storage, ClickHouse Keeper, Resources**: panels from the Prometheus metrics.

Grafana downloads the `grafana-clickhouse-datasource` plugin on first start; it is kept in a volume until `make down`.

## Notes

- `CLICKHOUSE_SKIP_USER_SETUP=1` keeps the passwordless `default` user reachable from other containers (clients
  and Grafana). This is for a local demo only.
- Materialized Views fire on INSERT only, not on replication. Each shard's SMT is filled by the replica that
  received the insert and replicated from there like any other part.
