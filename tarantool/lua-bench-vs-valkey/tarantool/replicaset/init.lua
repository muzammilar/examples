-- With BENCH_SYNC=1, create the two spaces of go/lua-bench/tarantool as synchronous
-- (is_sync = true) when this instance becomes the leader; the bench client's own
-- create(..., {if_not_exists = true}) then keeps them. Same formats as the client's schema.
local fiber = require('fiber')
local log = require('log')

if os.getenv('BENCH_SYNC') ~= '1' then return end

local function create_schema()
    local s = box.schema.space.create('bench_s', { is_sync = true, if_not_exists = true,
        format = { { 'key', 'string' }, { 'value', 'string' } } })
    s:create_index('pk', { parts = { 'key' }, if_not_exists = true })
    local h = box.schema.space.create('bench_h', { is_sync = true, if_not_exists = true,
        format = { { 'key', 'string' }, { 'version', 'unsigned' }, { 'fields', 'map' } } })
    h:create_index('pk', { parts = { 'key' }, if_not_exists = true })
    log.info('bench_s and bench_h are synchronous')
end

box.watch('box.status', function(_, status)
    if not status.is_ro then
        fiber.create(function()
            local ok, err = pcall(create_schema)
            if not ok then log.warn('create_schema: %s', err) end
        end)
    end
end)
