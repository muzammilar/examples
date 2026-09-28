-- `sysbench /bench/db.lua prepare|cleanup --mysql-db=information_schema`: (re)create/drop the
-- sbtest database (sysbench's own scripts expect it to exist), print the server's metadata as
-- `meta: key=value` lines for bench/report.py, and switch default_table_type to $TABLE_TYPE
-- (sysbench's CREATE TABLE has no table type; the variable is GLOBAL only) until cleanup
-- puts back $RESTORE_TABLE_TYPE.
function prepare()
   local con = sysbench.sql.driver():connect()
   con:query("DROP DATABASE IF EXISTS sbtest") -- left over from an interrupted run
   con:query("CREATE DATABASE sbtest")
   print("meta: server_version=" .. con:query_row("SELECT @@memsql_version"))
   print("meta: mysql_version=" .. con:query_row("SELECT @@version"))
   print("meta: leaves=" .. con:query_row("SELECT COUNT(*) FROM information_schema.LEAVES"))
   print("meta: partitions=" .. con:query_row(
      "SELECT COUNT(*) FROM information_schema.DISTRIBUTED_PARTITIONS " ..
      "WHERE DATABASE_NAME = 'sbtest' AND ROLE = 'Master'"))
   print("meta: default_table_type_before=" .. con:query_row("SELECT @@global.default_table_type"))
   con:query("SET GLOBAL default_table_type = '" .. os.getenv("TABLE_TYPE") .. "'")
end

function cleanup()
   local con = sysbench.sql.driver():connect()
   con:query("DROP DATABASE IF EXISTS sbtest")
   con:query("SET GLOBAL default_table_type = '" .. (os.getenv("RESTORE_TABLE_TYPE") or "columnstore") .. "'")
end

-- `sysbench /bench/db.lua storage --mysql-db=information_schema`: the table type sbtest got
sysbench.cmdline.commands = {
   storage = { function()
      print("meta: sbtest_storage_type=" .. sysbench.sql.driver():connect():query_row(
         "SELECT GROUP_CONCAT(DISTINCT STORAGE_TYPE) FROM information_schema.TABLES " ..
         "WHERE TABLE_SCHEMA = 'sbtest'"))
   end },
}
