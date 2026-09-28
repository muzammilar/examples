/* Run on ANY one data node. DDL in a Replicated database goes to every replica of it, on every shard, */
/* so no ON CLUSTER is needed (and it is not allowed inside a Replicated database). */

/* A local storage table. With no arguments ReplicatedMergeTree uses default_replica_path from */
/* config_overrides.xml: /clickhouse/tables/{database}.{table}/{shard} */
CREATE TABLE IF NOT EXISTS test.test_table_local
(
    `insert_timestamp` DateTime,
    `val` UInt64
)
ENGINE = ReplicatedMergeTree
PARTITION BY toYearWeek(insert_timestamp)
ORDER BY insert_timestamp
TTL insert_timestamp + toIntervalDay(120);

/* Create a SummingMergeTree (SMT) for hourly data */
CREATE TABLE IF NOT EXISTS test.test_table_hourly_smt_local
(
    `insert_timestamp` DateTime,
    `val` UInt64,
    `total` UInt64
)
ENGINE = ReplicatedSummingMergeTree
PARTITION BY toYearWeek(insert_timestamp)
ORDER BY (insert_timestamp, val)
TTL insert_timestamp + toIntervalDay(120);

/* Create a Materialized View from the base ingest table to the SMT. */
/* MVs fire on INSERT only, not on replication: each shard's SMT is filled by the inserting replica */
/* and then replicated like any other ReplicatedMergeTree part. */
CREATE MATERIALIZED VIEW IF NOT EXISTS test_mvs.test_table_hourly_smt_mv_local TO test.test_table_hourly_smt_local AS
SELECT
    toStartOfHour(insert_timestamp) AS insert_timestamp,
    val,
    count() as total
FROM test.test_table_local
GROUP BY
    insert_timestamp,
    val;
