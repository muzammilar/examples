# ClickHouse - Partial Updates with Dictionary

A minimal example of running a single server ClickHouse with a dictionary engine using XML based dictionary configuration.

## Testing

To test the ClickHouse behaviour, do the following:

```bash
# run the container
docker compose up

# ssh to clickhouse-server-01
docker exec -it clickhouse-server-01 bash

# read the data
clickhouse-client --multiline --multiquery --echo --format PrettyCompact --queries-file /ch-test/sql/91-*.sql
clickhouse-client --multiline --multiquery --echo --format PrettyCompact --queries-file /ch-test/sql/92-*.sql


# modify the data
clickhouse-client --multiline --multiquery --echo --format PrettyCompact --queries-file /ch-test/sql/03-*.sql

# read the data again
clickhouse-client --multiline --multiquery --echo --format PrettyCompact --queries-file /ch-test/sql/91-*.sql
clickhouse-client --multiline --multiquery --echo --format PrettyCompact --queries-file /ch-test/sql/92-*.sql


# delete everything after exiting the container
docker compose down --volumes

# delete container and volumes
docker compose rm --force --stop --volumes
```

## Notes

The following command will force a full reload of the dictionary:
```sql
SYSTEM RELOAD DICTIONARY test_dict;
```
