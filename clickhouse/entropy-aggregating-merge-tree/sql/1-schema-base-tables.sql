/* Create a local ingest table */
CREATE DATABASE IF NOT EXISTS test;

/* Create a separate database for Materialized Views in case you want to recreate them */
CREATE DATABASE IF NOT EXISTS test_mvs;

/* Create a database for views (for search) */
CREATE DATABASE IF NOT EXISTS test_views;

/* Create a local ingest table */
CREATE TABLE IF NOT EXISTS test.test_table_local
(
    `insert_timestamp` DateTime,
    `val32` UInt32,
    `val64` UInt64
)
ENGINE = MergeTree()
ORDER BY insert_timestamp;

/* Create an AggregatingMergeTree (AMT) for hourly data */
CREATE TABLE IF NOT EXISTS test.test_table_hourly_amt_local
(
    `insert_timestamp` DateTime,
    `val32_es` AggregateFunction(entropy, UInt32),
    `val64_es` AggregateFunction(entropy, UInt64)
)
ENGINE = AggregatingMergeTree
ORDER BY insert_timestamp;

/* Create a Materialized View from the base ingest table to the AMT */
CREATE MATERIALIZED VIEW IF NOT EXISTS test_mvs.test_table_hourly_amt_mv_local TO test.test_table_hourly_amt_local AS
SELECT
    toStartOfHour(insert_timestamp) AS insert_timestamp,
    entropyState(val32) AS val32_es,
    entropyState(val64) AS val64_es
FROM test.test_table_local
GROUP BY insert_timestamp;

/* Create an AggregatingMergeTree (AMT) for daily data but is identical to hourly table */
CREATE TABLE IF NOT EXISTS test.test_table_daily_amt_local
AS test.test_table_hourly_amt_local;

/* Check AggregatingMergeTree (AMT) for daily data but is identical to hourly table */
SHOW CREATE TABLE test.test_table_daily_amt_local;

/* Create a Materialized View from the hourly AMT to daily AMT */
CREATE MATERIALIZED VIEW IF NOT EXISTS test_mvs.test_table_daily_amt_mv_local TO test.test_table_daily_amt_local AS
SELECT
    toStartOfDay(insert_timestamp) AS insert_timestamp,
    entropyMergeState(val32_es) AS val32_es,
    entropyMergeState(val64_es) AS val64_es
FROM test.test_table_hourly_amt_local
GROUP BY insert_timestamp;

/* Create a View over hourly AMT */
CREATE VIEW IF NOT EXISTS test_views.test_table_hourly_entropy_local AS
SELECT
    insert_timestamp,
    entropyMerge(val32_es) AS val32_entropy,
    entropyMerge(val64_es) AS val64_entropy
FROM test.test_table_hourly_amt_local
GROUP BY insert_timestamp;

/* Create a View over daily AMT */
CREATE VIEW IF NOT EXISTS test_views.test_table_daily_entropy_local AS
SELECT
    insert_timestamp,
    entropyMerge(val32_es) AS val32_entropy,
    entropyMerge(val64_es) AS val64_entropy
FROM test.test_table_daily_amt_local
GROUP BY insert_timestamp;



;;
