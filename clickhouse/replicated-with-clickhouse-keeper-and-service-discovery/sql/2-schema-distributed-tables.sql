/* Decrease DDL timeout to 5 seconds in case some server is down. */
SET distributed_ddl_task_timeout = 5;

/* Create a database to store the distributed table */
CREATE DATABASE IF NOT EXISTS test;

/* Create a distributed table for raw data */
/* Note: we don't specify a sharding key here */
CREATE TABLE IF NOT EXISTS test.test_table AS test.test_table_local
ENGINE = Distributed(cluster_hits, test, test_table_local);

/* Create a distributed table for SMT */
CREATE TABLE IF NOT EXISTS test.test_table_hourly_smt AS test.test_table_hourly_smt_local
ENGINE = Distributed(cluster_hits, test, test_table_hourly_smt_local);

;;
