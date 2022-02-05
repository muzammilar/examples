
/* Get Information from the keeper */

SELECT * FROM system.zookeeper WHERE path='/clickhouse';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_hourly_smt_local';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_hourly_smt_local/001';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001/replicas';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001/replicas/ch04';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001/replicas/ch04/parts';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001/replicas/ch04/queue';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001/replicas/ch04/is_active';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001/replicas/ch04/is_lost';

/* Dropping Dead Replica: https://clickhouse.com/docs/en/sql-reference/statements/system/#query_language-system-drop-replica */
/* Live replicas use `DROP TABLE` */

;;
