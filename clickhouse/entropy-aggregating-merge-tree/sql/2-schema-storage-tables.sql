
/* Note: The following behaviour is only possible when you have single
   instance of ClickHouse running (since Materialized Views don't
   mix with Distributed Engine, as Distributed Engines don't store data)
   Materialized Views won't work on top of Views either, for the same reason */

/* Note: Since the aggregating happens only on a subset of data,
   i.e each batch insert, the entropy computed here will be each batch,
   not entire day */


/* Create a database for latest entropy */
CREATE DATABASE IF NOT EXISTS test_results;

/* Create a table for storing latest entropy (replacing) */
CREATE TABLE test_results.test_table_entropy_batch_insert_latest_local
(
    `insert_timestamp` DateTime,
    `val32_entropy` Float64,
    `val64_entropy` Float64,
    `version_ts` DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version_ts)
ORDER BY insert_timestamp;

/* Create a table for storing entropy for each batched insert (non-replacing) */
CREATE TABLE test_results.test_table_entropy_batch_insert_all_local
(
    `insert_timestamp` DateTime,
    `val32_entropy` Float64,
    `val64_entropy` Float64,
    `batch_uuid` UUID,
    `version_ts` DateTime DEFAULT now()
)
ENGINE = MergeTree()
ORDER BY insert_timestamp;

/* Create MV to ReplacingMergeTree for the entropy */
CREATE MATERIALIZED VIEW IF NOT EXISTS test_mvs.test_table_entropy_batch_insert_latest_mv_local TO test_results.test_table_entropy_batch_insert_latest_local AS
SELECT
    insert_timestamp,
    entropyMerge(val32_es) AS val32_entropy,
    entropyMerge(val64_es) AS val64_entropy
FROM test.test_table_hourly_amt_local
GROUP BY insert_timestamp;

/* Create MV to MergeTree (non-replacing) for the entropy */
CREATE MATERIALIZED VIEW IF NOT EXISTS test_mvs.test_table_entropy_batch_insert_all_mv_local TO test_results.test_table_entropy_batch_insert_all_local AS
SELECT
    insert_timestamp,
    entropyMerge(val32_es) AS val32_entropy,
    entropyMerge(val64_es) AS val64_entropy,
    generateUUIDv4() AS batch_uuid
FROM test.test_table_hourly_amt_local
GROUP BY insert_timestamp;



;;
