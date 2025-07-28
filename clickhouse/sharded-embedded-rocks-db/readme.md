# ClickHouse - Sharded EmbeddedRocksDB

**NOTE: Replication is currently NOT supported.**

**NOTE: Distributed Database Engine Only Copies the Metadata Not the Actual Stored Data.**

In the following example, there are total of `12` nodes. There are `3` nodes forming a clickhouse-keeper coordination system and `9` nodes as part of the `cluster_hits` data cluster.

The cluster is monitored using [Prometheus](https://prometheus.io/) with each server sharing server metrics on port `8001`.

## Known Limitation


**(As of Jan 2025) We Can NOT use the Replicated Database Engine with EmbeddedRocksDB Table Engine for _Data Replication_ since RocksDB doesn't support replication itself. You can use a KeeperMap.**

**DatabaseReplicated is a simple tool you can use for most installations where you have homogeneous nodes in cluster. It is easy to use and you only need to create them once per node. There is no automation to that replicates databases itself so if you have 1000 databases you will need to create all of them on each new node in cluster when you add them.**

By default, the EmbeddedRocksDB table is optimized for bulk inserts. In our case, we don't want bulk inserts, so we have to force data flush more often because we want to read this data on other nodes and it won't be available on other nodes if it's not flushed. Flush is a _very_ expensive operation.

Since our RocksDB database is _shared_, we don't have a way to determine where the data will be written. So in order to force writing of the data;
* Either - Write the data using the _distributed_ engine on top of the RocksDB engine. Caveat: Since we can't have replication, we can only have one replica per shard.
* Or - Don't write at all (i.e. read-only) and have the data pre-sharded

## Testing

To test the ClickHouse behaviour, do the following:

```bash
# run the containers
docker compose up --detach --build

# ssh to clickhouse-server-01 (or any of other servers)
# and use the clickhouse-client to run the queries
# under `sql` which maps to `/ch-replica-sql` in the container
docker exec -it clickhouse-server-01 bash

# debug command (to debug configs): `docker-compose run clickhouse-server-01 bash`

clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/1-schema-base-mergetree.sql
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/1-schema-base-rocksdb.sql
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/2-schema-distributed-mergetree.sql
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/2-schema-distributed-rocksdb.sql

# add more data and see how the cluster behaves (for example SMT)
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/3-insert.sql

# get results
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/4-results.sql

# get state from keeper (optional)
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/5-keeper.sql
# Check both replicated and unreplicated SMTs on all server
# The unreplicated SMT would only have data on the insert server and not on the replica servers, since Materialized Views are triggered
# only on insert and NOT on replication.

# Testing
watch -d -n 1 "clickhouse-client --query 'select * from test_replicated.test_rocks' --format Pretty"

```



## Cleanup

```sh
# Exit the container and cleanup
docker compose down --volumes

# Use `docker compose down --rmi all --volumes` with above to images as well
# Remove everything (and remove volumes). Networks are not removed here.
docker compose rm --force --stop -v

# if the container is already stopped
docker compose rm --force -v

```
