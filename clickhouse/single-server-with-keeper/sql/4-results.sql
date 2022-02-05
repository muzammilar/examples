
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

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test-test_table_local/0001/replicas';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test-test_table_local/0001/replicas/clickhouse-server-01';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test-test_table_local/0001/replicas/clickhouse-server-01/parts';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test-test_table_local/0001/replicas/clickhouse-server-01/queue';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test-test_table_local/0001/replicas/clickhouse-server-01/is_active';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test-test_table_local/0001/replicas/clickhouse-server-01/is_lost';

/* Dropping Dead Replica: https://clickhouse.com/docs/en/sql-reference/statements/system/#query_language-system-drop-replica */
/* Live replicas use `DROP TABLE` */

;;
