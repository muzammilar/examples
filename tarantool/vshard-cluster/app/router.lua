-- Router: stateless, maps id -> bucket (crc32 of the key) -> replicaset, forwards calls.
local vshard = require('vshard')
local fiber = require('fiber')
local log = require('log')

-- Distribute the 3,000 buckets over the storage replicasets once they are all reachable.
fiber.create(function()
    while true do
        local ok, err = vshard.router.bootstrap({ if_not_bootstrapped = true })
        if ok then log.info('vshard bootstrapped') return end
        log.info('vshard bootstrap: %s, retrying', err)
        fiber.sleep(1)
    end
end)

local function call(mode, id, fn, args)
    local bucket_id = vshard.router.bucket_id_mpcrc32({ id })
    local res, err = vshard.router[mode](bucket_id, fn, args or { id }, { timeout = 2 })
    if err ~= nil then error(err) end
    return res, bucket_id
end

function put(id, value)
    local bucket_id = vshard.router.bucket_id_mpcrc32({ id })
    local res, err = vshard.router.callrw(bucket_id, 'kv_put', { id, bucket_id, value }, { timeout = 2 })
    if err ~= nil then error(err) end
    return res
end

function get(id)
    return (call('callro', id, 'kv_get'))
end

-- per replicaset: active buckets on its master and rows in `kv`
function buckets()
    local out = {}
    for uuid, rs in pairs(vshard.router.routeall()) do
        local info = rs:callrw('vshard.storage.info', {}, { timeout = 2 })
        local name = rs.name or uuid
        out[name] = {
            active = info and info.bucket.active or -1,
            sending = info and info.bucket.sending or -1,
            receiving = info and info.bucket.receiving or -1,
            rows = rs:callrw('kv_len', {}, { timeout = 2 }) or -1,
        }
    end
    return out
end

-- number of ids not found where their bucket lives. Grouped by bucket and sent with
-- callrw(bucket_id, ...), so vshard pins each bucket on its storage for the call: correct even
-- while the rebalancer is moving buckets.
function check(ids)
    local by_bucket = {}
    for _, id in ipairs(ids) do
        local b = vshard.router.bucket_id_mpcrc32({ id })
        by_bucket[b] = by_bucket[b] or {}
        table.insert(by_bucket[b], id)
    end
    local missing = 0
    for b, list in pairs(by_bucket) do
        -- retried: right after a replicaset is removed the router may still hold a route to it
        -- (NO_CONNECTION, getaddrinfo) until it rediscovers the bucket
        for attempt = 1, 20 do
            local m, err = vshard.router.callrw(b, 'kv_missing', { list }, { timeout = 10 })
            if err == nil then missing = missing + m break end
            if attempt == 20 then error(err) end
            fiber.sleep(0.5)
        end
    end
    return missing
end

-- true when replicaset `name` holds exactly n active buckets and no bucket is in flight anywhere
function settled(name, n)
    local b = buckets()
    if b[name] == nil then return n == 0 end
    for _, x in pairs(b) do
        if x.sending ~= 0 or x.receiving ~= 0 then return false end
    end
    return b[name].active == n
end

-- one line per replicaset for `make status`
function buckets_text()
    local b, names, lines = buckets(), {}, {}
    for name in pairs(b) do table.insert(names, name) end
    table.sort(names)
    for _, name in ipairs(names) do
        table.insert(lines, ('%s active=%d sending=%d receiving=%d rows=%d'):format(
            name, b[name].active, b[name].sending, b[name].receiving, b[name].rows))
    end
    return lines
end
