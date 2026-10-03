-- Delete the given hashes, optionally only those at an expected version.
-- KEYS item keys, ARGV[1] expected version ('' for any)
-- Returns the number of keys deleted.
local deleted = 0
for _, key in ipairs(KEYS) do
  local current = redis.call('HGET', key, 'version')
  if current and (ARGV[1] == '' or ARGV[1] == current) then
    deleted = deleted + redis.call('DEL', key)
  end
end
return deleted
