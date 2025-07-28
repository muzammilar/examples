/* Decrease DDL timeout to 10 seconds in case some server is down. */
SET distributed_ddl_task_timeout = 10;

/* Create a database for storage table */
CREATE DATABASE IF NOT EXISTS test;

/* Create a local storage table */
/* Note: using `{table}` in Replicated can cause issues when doing ALTER RENAME on the table since it will expand to different path */
/* Note: Macro '{uuid}' and empty arguments of ReplicatedMergeTree are supported only for ON CLUSTER queries with Atomic database engine. */
CREATE TABLE IF NOT EXISTS test.test_table_local ON CLUSTER cluster_hits
(
    `insert_timestamp` DateTime,
    `val` UInt64
)
ENGINE = ReplicatedMergeTree
PARTITION BY toYearWeek(insert_timestamp)
ORDER BY insert_timestamp
TTL insert_timestamp + toIntervalDay(120);

;;
