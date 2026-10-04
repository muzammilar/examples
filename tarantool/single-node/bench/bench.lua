-- `make benchmark`: a closed-loop load generator in Lua (tarantool image, net.box).
-- FIBERS fibers share CONNS connections (net.box pipelines concurrent requests over one
-- socket) and run each operation for DURATION seconds. Prints ops/s and latency
-- percentiles per operation; the same lines go to results/$NAME.txt.
local netbox = require('net.box')
local fiber = require('fiber')
local clock = require('clock')

local uri = os.getenv('TARANTOOL_URI') or 'app:secret@tarantool:3301'
local FIBERS = tonumber(os.getenv('FIBERS') or 64)
local CONNS = tonumber(os.getenv('CONNS') or 4)
local DURATION = tonumber(os.getenv('DURATION') or 10)
local KEYS = tonumber(os.getenv('KEYS') or 100000)
local OPS = os.getenv('OPS') or 'memtx_replace,memtx_get,vinyl_replace,vinyl_get,call_transfer'
local out = io.open('/results/' .. (os.getenv('NAME') or 'bench') .. '.txt', 'w')

local function say(fmt, ...)
    local line = fmt:format(...)
    print(line)
    if out then out:write(line, '\n') out:flush() end
end

local conns = {}
for i = 1, CONNS do
    conns[i] = netbox.connect(uri, { wait_connected = 10 })
    assert(conns[i]:is_connected(), tostring(conns[i].error))
end
local c = conns[1]
c:eval([[
for _, e in ipairs({'memtx', 'vinyl'}) do
    local n = 'bench_' .. e
    if box.space[n] then box.space[n]:drop() end
    box.schema.space.create(n, {engine = e}):create_index('pk')
end
]])
assert(c:eval('return box.space.accounts:len()') >= 1000, 'run `make test` first: transfer needs accounts 1..1000')
for _, cn in ipairs(conns) do cn:reload_schema() end -- pick up the new spaces
local total_before = c:call('total_balance')

local payload = string.rep('x', 100)
local ops = {
    memtx_replace = function(cn, k) cn.space.bench_memtx:replace({ k, payload }) end,
    memtx_get = function(cn, k) cn.space.bench_memtx:get(k) end,
    vinyl_replace = function(cn, k) cn.space.bench_vinyl:replace({ k, payload }) end,
    vinyl_get = function(cn, k) cn.space.bench_vinyl:get(k) end,
    call_transfer = function(cn, k)
        local a = k % 1000 + 1
        local b = (k * 7 + 13) % 1000 + 1
        if a == b then b = b % 1000 + 1 end
        -- insufficient funds is a valid business outcome, not a failure
        local ok, err = pcall(cn.call, cn, 'transfer', { a, b, 1 })
        if not ok and not tostring(err):find('insufficient funds') then error(err) end
    end,
}

say('meta: date=%s tarantool=%s fibers=%d conns=%d duration_s=%d keys=%d limits=%s',
    os.date('!%Y-%m-%dT%H:%M:%SZ'), c:eval('return box.info.version'), FIBERS, CONNS, DURATION, KEYS,
    os.getenv('BENCH_LIMITS') or 'none')
say('%-14s %10s %9s %9s %9s %9s %7s', 'op', 'ops/s', 'p50 ms', 'p99 ms', 'p99.9 ms', 'max ms', 'errors')

for name in OPS:gmatch('[^,]+') do
    local op = assert(ops[name], 'unknown op ' .. name)
    local lat, errors, stop, kinds = {}, 0, false, {}
    local done = fiber.channel(FIBERS)
    local t0 = clock.monotonic()
    for f = 1, FIBERS do
        fiber.create(function()
            local cn = conns[(f - 1) % CONNS + 1]
            local local_lat = {}
            while not stop do
                local k = math.random(KEYS)
                local s = clock.monotonic()
                local ok, err = pcall(op, cn, k)
                if ok then
                    table.insert(local_lat, clock.monotonic() - s)
                else
                    errors = errors + 1
                    local kind = tostring(err)
                    kinds[kind] = (kinds[kind] or 0) + 1
                    fiber.yield() -- an error raised before any I/O would never yield
                end
            end
            done:put(local_lat)
        end)
    end
    fiber.sleep(DURATION)
    stop = true
    for _ = 1, FIBERS do
        for _, v in ipairs(done:get()) do table.insert(lat, v) end
    end
    local elapsed = clock.monotonic() - t0
    table.sort(lat)
    local function pct(p) return (lat[math.max(1, math.ceil(#lat * p))] or 0) * 1000 end
    say('%-14s %10.0f %9.3f %9.3f %9.3f %9.3f %7d', name, #lat / elapsed, pct(0.50), pct(0.99), pct(0.999),
        (lat[#lat] or 0) * 1000, errors)
    for kind, n in pairs(kinds) do say('  error x%d: %s', n, kind) end
end

local total_after = c:call('total_balance')
say('check: total_balance before=%d after=%d %s', total_before, total_after,
    total_before == total_after and 'OK' or 'MISMATCH')
c:eval("box.space.bench_memtx:drop() box.space.bench_vinyl:drop()")
if out then out:close() end
os.exit(total_before == total_after and 0 or 1)
