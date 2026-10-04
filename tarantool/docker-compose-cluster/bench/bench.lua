-- `make benchmark`: closed-loop net.box load generator against the leader. FIBERS fibers share
-- CONNS connections and run each op for DURATION s: replace into an asynchronous space (commit
-- after the leader's WAL write), into a synchronous one (commit after 2 of 3 WALs), and get.
local netbox = require('net.box')
local fiber = require('fiber')
local clock = require('clock')

local FIBERS = tonumber(os.getenv('FIBERS') or 64)
local CONNS = tonumber(os.getenv('CONNS') or 4)
local DURATION = tonumber(os.getenv('DURATION') or 10)
local KEYS = tonumber(os.getenv('KEYS') or 100000)
local OPS = os.getenv('OPS') or 'async_replace,sync_replace,get'
local out = io.open('/results/' .. (os.getenv('NAME') or 'bench') .. '.txt', 'w')
local function say(fmt, ...)
    local line = fmt:format(...)
    print(line)
    if out then out:write(line, '\n') out:flush() end
end

local leader_uri
for u in (os.getenv('TARANTOOL_URIS') or ''):gmatch('[^,]+') do
    local c = netbox.connect(u, { wait_connected = 5 })
    if c:is_connected() and c:eval('return box.info.ro') == false then leader_uri = u end
    c:close()
end
assert(leader_uri, 'no leader')
local conns = {}
for i = 1, CONNS do conns[i] = netbox.connect(leader_uri, { wait_connected = 10 }) end
local c = conns[1]
c:eval('box.space.kv_async:truncate() box.space.kv_sync:truncate()')

local payload = string.rep('x', 100)
local ops = {
    async_replace = function(cn, k) cn.space.kv_async:replace({ k, payload }) end,
    sync_replace = function(cn, k) cn.space.kv_sync:replace({ k, payload }) end,
    get = function(cn, k) cn.space.kv_sync:get(k) end,
}

say('meta: date=%s tarantool=%s leader=%s fibers=%d conns=%d duration_s=%d keys=%d limits=%s',
    os.date('!%Y-%m-%dT%H:%M:%SZ'), c:eval('return box.info.version'), c:eval('return box.info.name'),
    FIBERS, CONNS, DURATION, KEYS, os.getenv('BENCH_LIMITS') or 'none')
say('%-14s %10s %9s %9s %9s %9s %7s', 'op', 'ops/s', 'p50 ms', 'p99 ms', 'p99.9 ms', 'max ms', 'errors')
for name in OPS:gmatch('[^,]+') do
    local op = assert(ops[name], 'unknown op ' .. name)
    local lat, errors, stop, kinds = {}, 0, false, {}
    local done = fiber.channel(FIBERS)
    local t0 = clock.monotonic()
    for f = 1, FIBERS do
        fiber.create(function()
            local cn, l = conns[(f - 1) % CONNS + 1], {}
            while not stop do
                local s = clock.monotonic()
                local ok, err = pcall(op, cn, math.random(KEYS))
                if ok then table.insert(l, clock.monotonic() - s) else
                    errors = errors + 1
                    kinds[tostring(err)] = (kinds[tostring(err)] or 0) + 1
                    fiber.yield()
                end
            end
            done:put(l)
        end)
    end
    fiber.sleep(DURATION)
    stop = true
    for _ = 1, FIBERS do for _, v in ipairs(done:get()) do table.insert(lat, v) end end
    local elapsed = clock.monotonic() - t0
    table.sort(lat)
    local function pct(p) return (lat[math.max(1, math.ceil(#lat * p))] or 0) * 1000 end
    say('%-14s %10.0f %9.3f %9.3f %9.3f %9.3f %7d', name, #lat / elapsed, pct(0.50), pct(0.99), pct(0.999),
        (lat[#lat] or 0) * 1000, errors)
    for kind, n in pairs(kinds) do say('  error x%d: %s', n, kind) end
end
if out then out:close() end
os.exit(0) -- flush stdout; open connections would keep the event loop running
