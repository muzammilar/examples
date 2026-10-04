-- Tarantool version of go/rueidis-lua-bench/lua/update.lua: set fields on an existing item and
-- bump its version, only if the version is the expected one ('' for any).
-- Returns the new version, 0 if the key is missing, -1 on a version mismatch.
function bench_update(key, expected, fields)
    local t = box.space.bench_h:get(key)
    if t == nil then return 0 end
    if expected ~= '' and t.version ~= tonumber(expected) then return -1 end
    local merged = t.fields
    for k, v in pairs(fields) do merged[k] = v end
    box.space.bench_h:replace({ key, t.version + 1, merged })
    return t.version + 1
end
