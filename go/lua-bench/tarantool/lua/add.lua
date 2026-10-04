-- Tarantool version of go/rueidis-lua-bench/lua/add.lua: create an inventory item only if the
-- key does not exist; it starts at version 1. Items live in space bench_h as
-- {key, version, fields} with fields a map such as {name = 'item', qty = '1'}.
-- Returns 1 if created, 0 if the key already exists.
function bench_add(key, fields)
    if box.space.bench_h:get(key) ~= nil then return 0 end
    box.space.bench_h:insert({ key, 1, fields })
    return 1
end
