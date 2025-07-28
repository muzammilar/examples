/* Decrease DDL timeout to 10 seconds in case some server is down. */
SET distributed_ddl_task_timeout = 10;

/* Create a database for rocksdb */
/* Note: This doesn't work because I am using Replicated Database Engine incorrectly. The default Atomic Engine would be okay */
CREATE DATABASE IF NOT EXISTS test_replicated
ON CLUSTER cluster_hits
ENGINE = Replicated('/clickhouse/databases/{database}', '{shard}', '{replica}');

/* Create a table for rocksdb */
/* ON CLUSTER is not supported for Replicated Database Engine */
CREATE TABLE IF NOT EXISTS test_replicated.test_rocks_local
(
    `mykey` UInt64,
    `v1` UInt32,
    `v2` String,
    `v3` Float32
)
ENGINE = EmbeddedRocksDB()
PRIMARY KEY mykey
SETTINGS
    bulk_insert_block_size = 1;     /* disable bulk insert optimization so we write to disk always */


;;
