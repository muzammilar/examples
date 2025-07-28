/* Decrease DDL timeout to 10 seconds in case some server is down. */
SET distributed_ddl_task_timeout = 10;

/* Create a database to store the distributed table */
CREATE DATABASE IF NOT EXISTS test_replicated ON CLUSTER cluster_hits;

/* Create a distributed table for raw data */
/* Note: we specify a sharding key here to allow data writes */
/* Note: Do not specify ON CLUSTER here since we have a Replicated Database Engine - Not that it works */
CREATE TABLE IF NOT EXISTS test_replicated.test_rocks AS test_replicated.test_rocks_local
ENGINE = Distributed(cluster_hits, test_replicated, test_rocks_local, mykey);

;;
