/* Create a database for storage table */
CREATE DATABASE IF NOT EXISTS test ON CLUSTER replicated_with_keeper;

/* Create a separate database for Materialized Views in case you want to recreate them */
CREATE DATABASE IF NOT EXISTS test_mvs ON CLUSTER replicated_with_keeper;

/* Create a database for views (for search) */
CREATE DATABASE IF NOT EXISTS test_views ON CLUSTER replicated_with_keeper;

/* Create a local storage table */
/* Note: using `{table}` in Replicated can cause issues when doing ALTER RENAME on the table since it will expand to different path */
CREATE TABLE IF NOT EXISTS test.test_table_local ON CLUSTER replicated_with_keeper
(
    `insert_timestamp` DateTime,
    `val` UInt64
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{datacenter}-{layer}-{shard}/{database}/{table}', '{replica}')
PARTITION BY toYearWeek(insert_timestamp)
ORDER BY insert_timestamp
TTL insert_timestamp + toIntervalDay(120);

/* Create a SummingMergeTree (SMT) for hourly data */
CREATE TABLE IF NOT EXISTS test.test_table_hourly_smt_local  ON CLUSTER replicated_with_keeper
(
    `insert_timestamp` DateTime,
    `val` UInt64,
    `total` UInt64
)
ENGINE = ReplicatedSummingMergeTree('/clickhouse/tables/{datacenter}-{layer}-{shard}/{database}/{table}', '{replica}')
PARTITION BY toYearWeek(insert_timestamp)
ORDER BY (insert_timestamp, val)
TTL insert_timestamp + toIntervalDay(120);

/* Create a Materialized View from the base ingest table to the SMT */
CREATE MATERIALIZED VIEW IF NOT EXISTS test_mvs.test_table_hourly_smt_mv_local  ON CLUSTER replicated_with_keeper TO test.test_table_hourly_smt_local AS
SELECT
    toStartOfHour(insert_timestamp) AS insert_timestamp,
    val,
    count() as total
FROM test.test_table_local
GROUP BY
    insert_timestamp,
    val;



;;
