-- Storage side of the go/lua-bench/tarantool workload on vshard: the same spaces and functions
-- as go/lua-bench/tarantool/lua/*.lua plus a bucket_id field (vshard moves rows by it).
--   bench_s {key, bucket_id, value}
--   bench_h {key, bucket_id, version, fields}
local fiber = require('fiber')
local log = require('log')

local function create_schema()
    local s = box.schema.space.create('bench_s', { if_not_exists = true, format = {
        { 'key', 'string' }, { 'bucket_id', 'unsigned' }, { 'value', 'string' } } })
    s:create_index('pk', { parts = { 'key' }, if_not_exists = true })
    s:create_index('bucket_id', { parts = { 'bucket_id' }, unique = false, if_not_exists = true })
    local h = box.schema.space.create('bench_h', { if_not_exists = true, format = {
        { 'key', 'string' }, { 'bucket_id', 'unsigned' }, { 'version', 'unsigned' }, { 'fields', 'map' } } })
    h:create_index('pk', { parts = { 'key' }, if_not_exists = true })
    h:create_index('bucket_id', { parts = { 'bucket_id' }, unique = false, if_not_exists = true })
end

box.watch('box.status', function(_, status)
    if not status.is_ro then
        fiber.create(function()
            local ok, err = pcall(create_schema)
            if not ok then log.warn('create_schema: %s', err) end
        end)
    end
end)

function bench_truncate()
    box.space.bench_s:truncate()
    box.space.bench_h:truncate()
    return true
end

function bench_put(key, bucket_id, value)
    box.space.bench_s:replace({ key, bucket_id, value })
    return true
end

function bench_get(key)
    local t = box.space.bench_s:get(key)
    return t and t.value
end

-- 1 created, 0 exists
function bench_add(key, bucket_id, fields)
    if box.space.bench_h:get(key) ~= nil then return 0 end
    box.space.bench_h:insert({ key, bucket_id, 1, fields })
    return 1
end

-- new version, 0 missing, -1 version mismatch ('' = any version)
function bench_update(key, expected, fields)
    local t = box.space.bench_h:get(key)
    if t == nil then return 0 end
    if expected ~= '' and t.version ~= tonumber(expected) then return -1 end
    local merged = t.fields
    for k, v in pairs(fields) do merged[k] = v end
    box.space.bench_h:replace({ key, t.bucket_id, t.version + 1, merged })
    return t.version + 1
end

-- number deleted ('' = any version)
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
