
/* Get Information from the keeper */

SELECT * FROM system.zookeeper WHERE path='/clickhouse';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_hourly_smt_local';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_hourly_smt_local/001';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001/replicas';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001/replicas/clickhouse-server-04';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001/replicas/clickhouse-server-04/parts';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001/replicas/clickhouse-server-04/queue';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001/replicas/clickhouse-server-04/is_active';

SELECT * FROM system.zookeeper WHERE path='/clickhouse/tables/test.test_table_local/001/replicas/clickhouse-server-04/is_lost';

/* Dropping Dead Replica: https://clickhouse.com/docs/en/sql-reference/statements/system/#query_language-system-drop-replica */
/* Live replicas use `DROP TABLE` */


/* Replicated databases: one replica entry per data node, named 'shard|replica' */
SELECT name FROM system.zookeeper WHERE path = '/clickhouse/databases/test/replicas';
