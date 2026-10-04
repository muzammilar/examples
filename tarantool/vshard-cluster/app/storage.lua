-- Storage instances. vshard keeps its own _bucket space; `kv` holds the data, each row tagged
-- with its bucket_id (the rebalancer moves rows bucket by bucket through that index).
-- The schema is created when this instance is writable (the replicaset leader).
local fiber = require('fiber')
local log = require('log')

local function create_schema()
    local kv = box.schema.space.create('kv', {
        if_not_exists = true,
        format = {
            { name = 'id', type = 'unsigned' },
            { name = 'bucket_id', type = 'unsigned' },
            { name = 'value', type = 'string' },
        },
    })
    kv:create_index('pk', { parts = { 'id' }, if_not_exists = true })
    kv:create_index('bucket_id', { parts = { 'bucket_id' }, unique = false, if_not_exists = true })
end

box.watch('box.status', function(_, status)
    if not status.is_ro then
        fiber.create(function()
            local ok, err = pcall(create_schema)
            if not ok then log.warn('create_schema: %s', err) end
        end)
    end
end)

function kv_put(id, bucket_id, value)
    box.space.kv:replace({ id, bucket_id, value })
    return true
end

function kv_get(id)
    local t = box.space.kv:get(id)
    return t and t.value
end

-- how many of ids are not here (used by the router's check())
function kv_missing(ids)
    local m = 0
    for _, id in ipairs(ids) do
        if box.space.kv:get(id) == nil then m = m + 1 end
    end
    return m
end

function kv_len()
    return box.space.kv and box.space.kv:len() or 0
end
