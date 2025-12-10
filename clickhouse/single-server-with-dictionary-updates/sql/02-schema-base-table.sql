/* Create a database for storage table */
CREATE DATABASE IF NOT EXISTS test;

/* Create a table for basic data information */
CREATE TABLE IF NOT EXISTS test.test_table
(
    `key` UInt64,
    `val` UInt64,
    `data_timestamp` DateTime DEFAULT now(),
)
ENGINE = ReplacingMergeTree(data_timestamp) -- replacing merge tree so we update the value and timestamp if it already exists
PARTITION BY toYear(data_timestamp)
ORDER BY key;

/* Insert some deterministic data into the table */
INSERT INTO test.test_table (key, val)
SELECT
    number,
    pow(number, 3)
FROM system.numbers
LIMIT 15;

/* Create a table from the dictionary but without the timestamp */
CREATE TABLE IF NOT EXISTS test.test_dict_table
(
    `key` UInt64,
    `value` UInt64,
)
ENGINE = Dictionary('test_dict');

;;
