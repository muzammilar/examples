# ClickHouse - Random Examples

This directory contains a set of examples for testing the ClickHouse behaviour.

## Testing

To test the ClickHouse behaviour, do the following:

```bash
# run the container
docker compose up

# ssh to clickhouse-server-01
docker exec -it clickhouse-server-01 bash

# run the example (in this case it's 01-array-map-index.sql)
clickhouse-client --multiline --multiquery --echo --format PrettyCompact --queries-file /ch-test/sql/01-*.sql

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
