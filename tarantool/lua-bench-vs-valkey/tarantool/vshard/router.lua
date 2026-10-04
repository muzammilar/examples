-- Router side: the functions go/lua-bench/tarantool calls with -router. Each one hashes the
-- key to a bucket (crc32) and forwards the call to the storage that owns it (callrw: the
-- master, so reads see every acknowledged write, as on a replicaset leader).
local vshard = require('vshard')
local fiber = require('fiber')
local log = require('log')

fiber.create(function()
    while true do
        local ok, err = vshard.router.bootstrap({ if_not_bootstrapped = true })
        if ok then log.info('vshard bootstrapped') return end
        log.info('vshard bootstrap: %s, retrying', err)
        fiber.sleep(1)
    end
end)

local function rw(key, fn, args)
    local res, err = vshard.router.callrw(vshard.router.bucket_id_strcrc32(key), fn, args, { timeout = 10 })
    if err ~= nil then error(err) end
    return res
end

-- true once all 3,000 buckets are writable through this router (bench/run.sh waits for it)
function bench_ready()
    return vshard.router.info().bucket.available_rw == 3000
end

-- empty the spaces on every storage; returns the version like the client's schema step
function bench_reset()
    local _, err = vshard.router.map_callrw('bench_truncate', {}, { timeout = 30 })
    if err ~= nil then error(err) end
    return box.info.version
end

function bench_put(key, value)
    return rw(key, 'bench_put', { key, vshard.router.bucket_id_strcrc32(key), value })
end

function bench_get(key)
    return rw(key, 'bench_get', { key })
end

function bench_add(key, fields)
    return rw(key, 'bench_add', { key, vshard.router.bucket_id_strcrc32(key), fields })
end

function bench_update(key, expected, fields)
    return rw(key, 'bench_update', { key, expected, fields })
end

-- the bench passes one key per call; routed by the first
function bench_delete(keys, expected)
    return rw(keys[1], 'bench_delete', { keys, expected })
end
