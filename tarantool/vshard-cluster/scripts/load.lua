-- `make scale-out` / `make scale-in`: WRITERS fibers call put(id, value) on the router with
-- unique ids for DURATION seconds (plus one get() per put of a recent id). A failed put is
-- retried with the same id (put is a replace, so a retry is idempotent). Prints one line per
-- second, then asks the router to check every acknowledged id is on the replicaset that owns
-- its bucket. Exit 1 if any is missing.
local netbox = require('net.box')
local fiber = require('fiber')
local clock = require('clock')

local WRITERS = tonumber(os.getenv('WRITERS') or 16)
local DURATION = tonumber(os.getenv('DURATION') or 60)
local RUN = tonumber(os.getenv('RUN') or os.time()) -- id prefix, unique per run
local router = netbox.connect(os.getenv('ROUTER_URI') or 'app:secret@router-a-001:3301', { wait_connected = 10 })
assert(router:is_connected(), tostring(router.error))

local acked, sec = {}, { ok = 0, err = 0, gets = 0 }
local failed, kinds, stop = 0, {}, false
local done = fiber.channel(WRITERS)
for w = 1, WRITERS do
    acked[w] = {}
    fiber.create(function()
        local seq = 0
        while not stop do
            seq = seq + 1
            local id = (RUN % 100000) * 10000000000 + w * 100000000 + seq
            while not stop do
                local ok, err = pcall(router.call, router, 'put', { id, 'v' .. id }, { timeout = 5 })
                if ok then
                    table.insert(acked[w], id)
                    sec.ok = sec.ok + 1
                    break
                end
                failed, sec.err = failed + 1, sec.err + 1
                local kind = tostring(err):gsub('%d+', 'N'):sub(1, 160)
                kinds[kind] = (kinds[kind] or 0) + 1
                fiber.sleep(0.05)
            end
            if seq > 10 and pcall(router.call, router, 'get', { acked[w][#acked[w] - 5] }, { timeout = 5 }) then
                sec.gets = sec.gets + 1
            end
        end
        done:put(true)
    end)
end

local t0 = clock.monotonic()
for s = 1, DURATION do
    fiber.sleep(t0 + s - clock.monotonic())
    print(('t=%3ds  put/s=%6d  get/s=%6d  failed=%d'):format(s, sec.ok, sec.gets, sec.err))
    sec.ok, sec.err, sec.gets = 0, 0, 0
end
stop = true
for _ = 1, WRITERS do done:get() end

local total, missing = 0, 0
for w = 1, WRITERS do
    total = total + #acked[w]
    for i = 1, #acked[w], 5000 do
        missing = missing + router:call('check', { { unpack(acked[w], i, math.min(i + 4999, #acked[w])) } }, { timeout = 60 })
    end
end
print(('acknowledged puts: %d (%.0f/s), failed attempts (retried): %d, lost acknowledged puts: %d'):format(
    total, total / DURATION, failed, missing))
for kind, n in pairs(kinds) do print(('  error x%d: %s'):format(n, kind)) end
os.exit(missing == 0 and 0 or 1)
