-- Valkey version of the Tarantool transfer() procedure (same checks, same return codes).
-- KEYS[1] acct:<from>, KEYS[2] acct:<to>, KEYS[3] req:<req_id>; ARGV[1] amount
if redis.call('EXISTS', KEYS[3]) == 1 then return 2 end
local from = redis.call('GET', KEYS[1])
if not from or not redis.call('GET', KEYS[2]) then return 3 end
local amount = tonumber(ARGV[1])
if tonumber(from) < amount then return 1 end
redis.call('DECRBY', KEYS[1], amount)
redis.call('INCRBY', KEYS[2], amount)
redis.call('SET', KEYS[3], KEYS[1] .. ' ' .. KEYS[2] .. ' ' .. ARGV[1])
return 0
