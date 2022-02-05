# ClickHouse - Keeper

A minimal example of running a single server ClickHouse Keeper.
This type of custom image is good for running and validating ClickHouse SQL in integration tests for CI pipelines containing `Replicated*` or `Distributed` tables.

## Testing

To test the ClickHouse behaviour, do the following:

```bash
# run the container
docker-compose up --build --detach

# ssh to clickhouse-server-01
# and use the clickhouse-client to run the queries
# under `sql` which maps to `/ch-replica-sql` in the container
docker exec -it clickhouse-server-01 bash

clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/1-*.sql
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/2-*.sql

# add more data and see how the cluster behaves (for example SMT)
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/3-insert.sql

# get results
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/4-results.sql

# get state from keeper
clickhouse-client --multiline --multiquery --format Pretty < /ch-replica-sql/5-keeper.sql

# Exit the container and cleanup
docker-compose down --volumes

# Use `docker-compose down --rmi all --volumes` with above to images as well
# Remove everything (and remove volumes). Networks are not removed here.
docker-compose rm --force --stop -v

```
