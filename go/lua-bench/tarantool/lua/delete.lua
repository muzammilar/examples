-- Tarantool version of go/rueidis-lua-bench/lua/delete.lua: delete the given items, optionally
-- only those at the expected version ('' for any).
-- Returns the number deleted.
function bench_delete(keys, expected)
    local deleted = 0
    for _, key in ipairs(keys) do
        local t = box.space.bench_h:get(key)
        if t ~= nil and (expected == '' or t.version == tonumber(expected)) then
            box.space.bench_h:delete(key)
            deleted = deleted + 1
        end
    end
    return deleted
end
