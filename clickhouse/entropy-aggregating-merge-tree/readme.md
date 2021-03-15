# ClickHouse - AggregatingMergeTree Examples for `entropy` Aggregate Function

An example of computing entropy of integer datatypes grouped by every hour as well by day.

While this example focuses on UInt32 data types, the aggregate function supports other data types as well.

Similarly, while this example shows `entropy`, it can applied to other to other aggregate functions,
which can be found in the docs or using

```sql
    SELECT * FROM system.functions where is_aggregate=1 ORDER by name
```

*Note:* Please note that entropy computation is a relatively expensive (when compared to median or other aggregate functions), both in terms of computate, and intermediate storage.

## Testing

To test the ClickHouse behaviour, do the following:

```bash
# run the container
docker-compose up --detach

# ssh to the container container and use the clickhouse-client to run the queries
# under `sql` which maps to `/ch-amt-example-sql` in the container
docker exec -it clickhouse-server-1 bash

clickhouse-client --multiline --multiquery --format Pretty < /ch-amt-example-sql/1-*.sql
clickhouse-client --multiline --multiquery --format Pretty < /ch-amt-example-sql/2-*.sql
clickhouse-client --multiline --multiquery --format Pretty < /ch-amt-example-sql/3-*.sql
clickhouse-client --multiline --multiquery --format Pretty < /ch-amt-example-sql/4-*.sql

```
