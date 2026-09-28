-- `sysbench /bench/db.lua prepare|cleanup --mysql-db=mysql`: (re)create/drop the sbtest
-- database (sysbench's own scripts expect it to exist) and print the server's metadata
-- as `meta: key=value` lines for bench/report.py.
function prepare()
   local con = sysbench.sql.driver():connect()
   con:query("DROP DATABASE IF EXISTS sbtest") -- left over from an interrupted run
   con:query("CREATE DATABASE sbtest")
   -- libmariadb's default collation would not compare with the server's string literals
   con:query("SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci")
   print("meta: server_version=" .. con:query_row("SELECT @@version"))
   print("meta: data_nodes=" .. con:query_row(
      "SELECT COUNT(*) FROM ndbinfo.nodes WHERE status = 'STARTED'"))
   print("meta: no_of_replicas=" .. con:query_row(
      "SELECT v.config_value FROM ndbinfo.config_values v JOIN ndbinfo.config_params p " ..
      "ON p.param_number = v.config_param WHERE p.param_name = 'NoOfReplicas' LIMIT 1"))
   print("meta: data_memory_bytes=" .. con:query_row(
      "SELECT SUM(total) FROM ndbinfo.memoryusage WHERE memory_type = 'Data memory' AND node_id = 1"))
end

function cleanup()
   sysbench.sql.driver():connect():query("DROP DATABASE IF EXISTS sbtest")
end
