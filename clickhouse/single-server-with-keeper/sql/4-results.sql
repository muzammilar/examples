
/* GET Information from the keeper */

SELECT * from test.test_table_hourly_smt;

SELECT count() from test.test_table_hourly_smt;

SELECT uniq(val), uniqCombined(val) from test.test_table;

SELECT * FROM system.zookeeper WHERE path='/clickhouse';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test-test_table_hourly_smt_local';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test-test_table_hourly_smt_local/0001';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test-test_table_local';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test-test_table_local/0001';

;;
