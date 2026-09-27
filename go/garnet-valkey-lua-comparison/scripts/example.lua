-- Example Lua script for Redis
-- Gets the value of a key and returns it with a prefix
local key = KEYS[1]
local value = redis.call('GET', key)
if value then
    return 'Value from Lua: ' .. value
else
    return 'Key not found'
end
