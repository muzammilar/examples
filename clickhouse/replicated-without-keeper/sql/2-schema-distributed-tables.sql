/* Create a database to store the distributed table */
CREATE DATABASE IF NOT EXISTS test;

/* Create a distributed ingest table */
/* Note: we don't specify a sharding key here */
CREATE TABLE IF NOT EXISTS test.test_table AS test.test_table_local
ENGINE = Distributed(replicated_without_keeper, test, test_table_local)
SETTINGS
    fsync_after_insert=0,
    fsync_directories=0;


/* Create a distributed query table over SMT
Note: we don't specify a sharding key here
CREATE TABLE IF NOT EXISTS test.test_table_hourly_smt AS test.test_table_hourly_smt_local
ENGINE = Distributed(query_replicated_without_keeper, test, test_table_hourly_smt_local);
*/

;;
