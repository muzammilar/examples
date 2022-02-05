# ClickHouse - Replication using ClickHouse Keeper

An example of replicating data in ClickHouse using clickhouse-keeper. In the following example, there are total of `11` nodes. There are `5` nodes forming a clickhouse-keeper coordination system and `9` nodes as part of the `cluster_hits` data cluster.

The cluster is monitored using [Prometheus](https://prometheus.io/) with each server sharing server metrics on port `8001`.

## Testing

To test the ClickHouse behaviour, do the following:

```bash
# run the containers
docker-compose up --detach --build

# ssh to clickhouse-server-01 (or any of other servers)
# and use the clickhouse-client to run the queries
# under `sql` which maps to `/ch-replica-sql` in the container
docker exec -it clickhouse-server-01 bash

# debug command (to debug configs): `docker-compose run clickhouse-server-01 bash`

clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/1-*.sql
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/2-*.sql

# see the tables being created on other servers

# add more data and see how the cluster behaves (for example SMT)
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/3-insert.sql

# Check both replicated and unreplicated SMTs on all server
# The unreplicated SMT would only have data on the insert server and not on the replica servers, since Materialized Views are triggered
# only on insert and NOT on replication.

```



## Cleanup

```sh
# Exit the container and cleanup
docker-compose down --volumes

# Use `docker-compose down --rmi all --volumes` with above to images as well
# Remove everything (and remove volumes). Networks are not removed here.
docker-compose rm --force --stop -v

# if the container is already stopped
docker-compose rm --force -v

```
