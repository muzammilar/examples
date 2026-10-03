-- Create a hash only if the key does not exist; it starts at version 1.
-- KEYS[1] item key, ARGV field value [field value ...]
-- Returns 1 if created, 0 if the key already exists.
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 0
end
redis.call('HSET', KEYS[1], 'version', 1, unpack(ARGV))
return 1
