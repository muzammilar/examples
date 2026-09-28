-- A primary-key lookup is routed by the key's hash to the one fragment (and node) that
-- owns it: one pk operation. A filter on a non-key column scans every fragment, with
-- the condition pushed down to the data nodes: one table scan. The session's NDB API
-- counters, read before and after each query, show which operation ran.
USE demo;

EXPLAIN SELECT * FROM accounts WHERE id = 3;
EXPLAIN SELECT * FROM accounts WHERE balance > 100;

SELECT SUM(IF(variable_name = 'Ndb_api_pk_op_count_session', variable_value, 0)),
       SUM(IF(variable_name = 'Ndb_api_table_scan_count_session', variable_value, 0))
INTO @pk, @scan FROM performance_schema.session_status;
SELECT * FROM accounts WHERE id = 3;
SELECT SUM(IF(variable_name = 'Ndb_api_pk_op_count_session', variable_value, 0)) - @pk AS pk_ops,
       SUM(IF(variable_name = 'Ndb_api_table_scan_count_session', variable_value, 0)) - @scan AS table_scans
FROM performance_schema.session_status;

SELECT SUM(IF(variable_name = 'Ndb_api_pk_op_count_session', variable_value, 0)),
       SUM(IF(variable_name = 'Ndb_api_table_scan_count_session', variable_value, 0))
INTO @pk, @scan FROM performance_schema.session_status;
SELECT * FROM accounts WHERE balance > 100;
SELECT SUM(IF(variable_name = 'Ndb_api_pk_op_count_session', variable_value, 0)) - @pk AS pk_ops,
       SUM(IF(variable_name = 'Ndb_api_table_scan_count_session', variable_value, 0)) - @scan AS table_scans
FROM performance_schema.session_status;
