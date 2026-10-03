-- `sysbench /bench/db.lua prepare|cleanup --mysql-db=mysql`: (re)create / drop the sbtest
-- database (sysbench's own scripts expect it to exist).
function prepare()
   local con = sysbench.sql.driver():connect()
   con:query("DROP DATABASE IF EXISTS sbtest")
   con:query("CREATE DATABASE sbtest")
end

function cleanup()
   sysbench.sql.driver():connect():query("DROP DATABASE IF EXISTS sbtest")
end
