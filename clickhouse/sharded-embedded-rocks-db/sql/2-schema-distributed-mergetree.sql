/* Decrease DDL timeout to 10 seconds in case some server is down. */
SET distributed_ddl_task_timeout = 10;

/* Create a database to store the distributed table */
CREATE DATABASE IF NOT EXISTS test ON CLUSTER cluster_hits;

/* Create a distributed table for raw data */
/* Note: we don't specify a sharding key here */
CREATE TABLE IF NOT EXISTS test.test_table ON CLUSTER cluster_hits
AS test.test_table_local
ENGINE = Distributed(cluster_hits, test, test_table_local);

;;
