-- record UDF: runs on the server, next to the record
function add(rec, bin, n)
  rec[bin] = (rec[bin] or 0) + n
  aerospike:update(rec)
  return rec[bin]
end

-- stream UDF for AGGREGATE: sum a bin over the records a query returns
local function add_values(a, b) return a + b end
function sum(stream, bin)
  return stream : map(function(rec) return rec[bin] end) : reduce(add_values)
end
