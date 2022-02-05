/* Create a database to store the distributed table */
CREATE DATABASE IF NOT EXISTS test ON CLUSTER test_cluster;

/* Create a distributed table for raw data */
/* Note: we don't specify a sharding key here */
CREATE TABLE IF NOT EXISTS test.test_table ON CLUSTER test_cluster AS test.test_table_local
ENGINE = Distributed(test_cluster, test, test_table_local);

/* Create a distributed table for SMT */
CREATE TABLE IF NOT EXISTS test.test_table_hourly_smt ON CLUSTER test_cluster AS test.test_table_hourly_smt_local
ENGINE = Distributed(test_cluster, test, test_table_hourly_smt_local);

/* File break */

;;
