/* Run on ANY one data node. Distributed tables over the discovered cluster_hits: */
/* they always use the current membership, so added or removed nodes need no ALTER. */

/* Create a distributed table for raw data */
/* Note: we don't specify a sharding key here */
CREATE TABLE IF NOT EXISTS test.test_table AS test.test_table_local
ENGINE = Distributed(cluster_hits, test, test_table_local);

/* Create a distributed table for SMT */
CREATE TABLE IF NOT EXISTS test.test_table_hourly_smt AS test.test_table_hourly_smt_local
ENGINE = Distributed(cluster_hits, test, test_table_hourly_smt_local);
