-- `sysbench /bench/db.lua prepare|cleanup --mysql-db=mysql`: (re)create/drop the sbtest
-- database (sysbench's own scripts expect it to exist) and print the cluster's metadata
-- as `meta: key=value` lines for bench/report.py.
function prepare()
   local con = sysbench.sql.driver():connect()
   con:query("DROP DATABASE IF EXISTS sbtest") -- left over from an interrupted run
   con:query("CREATE DATABASE sbtest")
   print("meta: server_version=" .. con:query_row("SELECT @@version"))
   print("meta: tidb_version=" .. con:query_row(
      "SELECT version FROM information_schema.cluster_info WHERE type = 'tidb' LIMIT 1"))
   print("meta: tikv_stores_up=" .. con:query_row(
      "SELECT COUNT(*) FROM information_schema.tikv_store_status WHERE store_state_name = 'Up'"))
   print("meta: pd_members=" .. con:query_row(
      "SELECT COUNT(*) FROM information_schema.cluster_info WHERE type = 'pd'"))
   print("meta: max_replicas=" .. con:query_row(
      "SELECT `value` FROM information_schema.cluster_config " ..
      "WHERE type = 'pd' AND `key` = 'replication.max-replicas' LIMIT 1"))
end

function cleanup()
   local con = sysbench.sql.driver():connect()
   con:query("DROP DATABASE IF EXISTS sbtest")
   con:query("DROP DATABASE IF EXISTS tpcc")
end
