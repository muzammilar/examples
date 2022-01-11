# ClickHouse - Replication without Keeper

An example of replicating data in ClickHouse.

*Note:*  This example does not use a ClickHouse Keeper (ZooKeeper or otherwise). In this case there will no guarantee that all the replicas will have 100% the same data, so this scenario should be rarely used (i.e. in cases where data loss is acceptable). It might be possible to use this type of setupfor highly-available (where ordering of data is not needed).

In this case, data is replicated by having `internal_replication=false`. The data is inserted into `Distributed` table over `MergeTree` tables. Inserts over `Distributed` Engine is generally less efficient than `MergeTree` Engine. Using `fsync` options in distributed insert can further slow down the inserts.

The `SummingMergeTree` and `AggregatingMergeTree` (or other `MergeTree`) are triggered by the `MaterializedView` at insert time (note: they don't directly read the ingest table but rather the insert blocks). So the workflow for remote servers (in this example) looks like following; Ingest (remote) -> MaterializedViews (remote) -> SummingMergeTree (remote). This [presentation by Denny Crane](https://den-crane.github.io/Everything_you_should_know_about_materialized_views_commented.pdf) provides an excellent view about Materialized Views.

The `ON CLUSTER` notion (i.e. Distributed DDL queries) are only supported when a Keeper is used.

## Testing

To test the ClickHouse behaviour, do the following:

```bash
# run the containers
docker-compose up --detach

# ssh to clickhouse-server-01 container since it doesn't auto-create/insert the tables
# and use the clickhouse-client to run the queries
# under `sql` which maps to `/ch-replica-sql` in the container
docker exec -it clickhouse-server-1 bash

clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/1-*.sql
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/2-*.sql

# see the tables being populated from clickhouse-server-2 and clickhouse-server-3

# add more data and see how the cluster behaves
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/3-insert.sql

```
