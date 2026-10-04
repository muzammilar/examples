-- Record UDFs with the same semantics as go/rueidis-lua-bench/lua/*.lua.
-- A record has a `version` bin plus field bins. Registered as module `bench`.

-- Create the record only if it does not exist; it starts at version 1.
-- fields: map of bin name -> value.
-- Returns 1 if created, 0 if the record already exists.
function add(rec, fields)
  if aerospike:exists(rec) then
    return 0
  end
  rec['version'] = 1
  for name, value in map.pairs(fields) do
    rec[name] = value
  end
  aerospike:create(rec)
  return 1
end

-- Set fields on an existing record and bump its version.
-- expected: expected version (nil for any); fields: map of bin name -> value.
-- Returns the new version, 0 if the record is missing, -1 on a version mismatch.
function update(rec, expected, fields)
  if not aerospike:exists(rec) then
    return 0
  end
  local current = rec['version']
  if expected ~= nil and expected ~= current then
    return -1
  end
  for name, value in map.pairs(fields) do
    rec[name] = value
  end
  rec['version'] = current + 1
  aerospike:update(rec)
  return current + 1
end

-- Delete the record, optionally only at an expected version (nil for any).
-- Returns 1 if deleted, 0 otherwise.
function delete(rec, expected)
  if not aerospike:exists(rec) then
    return 0
  end
  if expected ~= nil and expected ~= rec['version'] then
    return 0
  end
  aerospike:remove(rec)
  return 1
end
