-- `sysbench /bench/analytics.lua time --mysql-db=demo`: runs each query below RUNS times on
-- one connection against the columnstore demo.orders table from sql/02-generate.sql (600,000
-- rows, SORT KEY (created_at), SHARD KEY (customer_id)) and prints each run's wall time.
-- The first run of a query shape includes SingleStore compiling its plan to machine code;
-- later runs reuse the compiled plan from the plan cache.
local ffi = require("ffi")
ffi.cdef [[
typedef struct { long tv_sec; long tv_nsec; } bench_timespec;
int clock_gettime(int clk_id, bench_timespec *tp);
]]
local ts = ffi.new("bench_timespec")
local function now_ms()
   ffi.C.clock_gettime(1, ts) -- CLOCK_MONOTONIC
   return tonumber(ts.tv_sec) * 1e3 + tonumber(ts.tv_nsec) / 1e6
end

local queries = {
   { "scan_aggregate", "SELECT COUNT(*), SUM(amount), AVG(amount), MIN(created_at), MAX(created_at) FROM orders" },
   { "group_by_month", "SELECT DATE_TRUNC('month', created_at) AS month, COUNT(*), SUM(amount) " ..
      "FROM orders GROUP BY 1 ORDER BY 1" },
   { "count_distinct", "SELECT COUNT(DISTINCT customer_id) FROM orders" },
   { "json_group_by", "SELECT attrs::$channel AS channel, COUNT(*), SUM(attrs::%items) FROM orders " ..
      "GROUP BY 1 ORDER BY 1" },
   { "range_one_week", "SELECT COUNT(*), SUM(amount) FROM orders " ..
      "WHERE created_at >= '2026-03-01' AND created_at < '2026-03-08'" },
   { "colocated_join", "SELECT r.name, c.tier, COUNT(*), SUM(o.amount) FROM orders o " ..
      "JOIN customers c ON c.customer_id = o.customer_id JOIN regions r ON r.region_id = c.region_id " ..
      "GROUP BY 1, 2 ORDER BY 1, 2" },
}

local function time_queries()
   local runs = tonumber(os.getenv("RUNS") or "5")
   local con = sysbench.sql.driver():connect()
   print("meta: orders_rows=" .. con:query_row("SELECT COUNT(*) FROM orders"))
   for _, q in ipairs(queries) do
      local times = {}
      local rows = 0
      for i = 1, runs do
         local t0 = now_ms()
         local rs = con:query(q[2])
         rows = rs.nrows
         for _ = 1, rs.nrows do rs:fetch_row() end
         rs:free()
         times[i] = string.format("%.2f", now_ms() - t0)
      end
      print(string.format("query %s: rows=%d ms=%s", q[1], rows, table.concat(times, ",")))
      print("query_sql " .. q[1] .. ": " .. q[2])
   end
end

sysbench.cmdline.commands = { time = { time_queries } }
