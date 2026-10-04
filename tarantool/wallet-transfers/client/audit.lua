-- Valkey audit: sum and minimum of acct:1..acct:N, number of req:* keys.
-- ARGV[1] N. Runs as one script (blocks the server for the scan; fine for an audit).
local n = tonumber(ARGV[1])
local sum, min = 0, nil
for id = 1, n do
    local b = tonumber(redis.call('GET', 'acct:' .. id))
    sum = sum + b
    if min == nil or b < min then min = b end
end
local transfers = redis.call('DBSIZE') - n
return { sum, min, transfers }
