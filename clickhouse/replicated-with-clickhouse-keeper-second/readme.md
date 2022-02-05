# ClickHouse - Replication using ClickHouse Keeper

An example of replicating data in ClickHouse using clickhouse-keeper. In the following example, there are `3` nodes forming a clickhouse-keeper coordination system and `9` nodes as part of the `replicated_with_keeper` data cluster.

The consensus port used by NuRaft is `9481` (and can be changed via the configurations). The keeper connection port used by the clients (i.e. clickhouse-server) is `9181` similar to zookeepers `2181`.

Use `system.clusters` or `system.zookeeper` tables to check out the cluster and the keeper views respectively.

While the clickhouse-keeper, in this example, runs on the same nodes as data nodes, they can be separated and run on different servers as well.

In this case, data is replicated by having `internal_replication=false`. The data is inserted into `MergeTree` table (i.e. locally). Inserts over `Distributed` Engine is generally less efficient than `MergeTree` Engine.

The `SummingMergeTree` and `AggregatingMergeTree` (or other `MergeTree`) are triggered by the `MaterializedView` at insert time (note: they don't directly read the ingest table but rather the insert blocks). So the workflow for remote servers (in this example) looks like following; Ingest (remote) -> MaterializedViews (remote) -> SummingMergeTree (remote). This [presentation by Denny Crane](https://den-crane.github.io/Everything_you_should_know_about_materialized_views_commented.pdf) provides an excellent view about Materialized Views.

This example uses `{dclocation}-{layer}-{shard}` as the unique shard identifier in clickhouse.

This example only supports the *one-shard-per-node* model (i.e. only one shard can be on a single node) due to performance reasons. if a user really needs to have multiple shards per node, they should check out [this blog by Altinity](https://altinity.com/blog/2018/5/10/circular-replication-cluster-topology-in-clickhouse) and use the `default_database` field in shard definition for supporting *multiple-shard-replicas-per-node* model for their [cluster](https://clickhouse.tech/docs/en/operations/system-tables/clusters/).

The `ON CLUSTER` notion (i.e. Distributed DDL queries) are only supported when a Keeper is used.

## Testing

To test the ClickHouse behaviour, do the following:

```bash
# run the containers
docker-compose up --detach

# ssh to clickhouse-server-01 (or any of other servers)
# and use the clickhouse-client to run the queries
# under `sql` which maps to `/ch-replica-sql` in the container
docker exec -it clickhouse-server-01 bash

clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/1-*.sql
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/2-*.sql

# see the tables being created on other servers

# add more data and see how the cluster behaves (for example SMT)
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/3-insert.sql

# Check both replicated and unreplicated SMTs on all server
# The unreplicated SMT would only have data on the insert server and not on the replica servers, since Materialized Views are triggered
# only on insert and NOT on replication.

```


Delete *all* unused data after using docker (*Note*: It might have unintended consequences)

```sh
# less safe option (since it deletes lots of build caches, etc)
docker system prune --volumes

# alternatively, delete all dangling volumes
docker volume prune
```
