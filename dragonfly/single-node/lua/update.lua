-- Set fields on an existing hash and bump its version.
-- KEYS[1] item key, ARGV[1] expected version ('' for any), ARGV[2..] field value ...
-- Returns the new version, 0 if the key is missing, -1 on a version mismatch.
local current = redis.call('HGET', KEYS[1], 'version')
if not current then
  return 0
end
if ARGV[1] ~= '' and ARGV[1] ~= current then
  return -1
end
redis.call('HSET', KEYS[1], unpack(ARGV, 2))
return redis.call('HINCRBY', KEYS[1], 'version', 1)
