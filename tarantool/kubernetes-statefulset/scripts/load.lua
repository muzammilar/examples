-- `make failover`: WRITERS fibers insert into the synchronous `ledger` space on whichever
-- instance is the leader, for DURATION seconds. On an error a writer finds the leader again and
-- retries the same id; `Duplicate key` on a retry means the earlier attempt had committed, so it
-- counts as acknowledged. Prints one line per second, then checks that every acknowledged id is
-- on the leader and that all three instances have the same row count. Exit 1 on a lost write.
local netbox = require('net.box')
local fiber = require('fiber')
local clock = require('clock')

local WRITERS = tonumber(os.getenv('WRITERS') or 16)
local DURATION = tonumber(os.getenv('DURATION') or 40)
local uris = {}
for u in (os.getenv('TARANTOOL_URIS') or ''):gmatch('[^,]+') do table.insert(uris, u) end

local conns = {}
for i, u in ipairs(uris) do
    -- reconnect_after: net.box keeps trying in the background after the peer dies
    conns[i] = netbox.connect(u, { wait_connected = false, reconnect_after = 0.2 })
end

local leader, leader_name = nil, '?'
local function find_leader()
    for _ = 1, 200 do
        for _, c in ipairs(conns) do
            if c:is_connected() then
                local ok, ro, name = pcall(c.eval, c, 'return box.info.ro, box.info.name', {}, { timeout = 0.5 })
                if ok and ro == false then leader, leader_name = c, name return end
            end
        end
        fiber.sleep(0.05)
    end
    error('no leader found within 10 s')
end
find_leader()
-- start from an empty ledger
leader:eval('box.space.ledger:truncate()')

local acked, failed, sec_ok, sec_err = {}, 0, 0, 0
local max_gap, errors_seen = 0, {}
local stop = false
local done = fiber.channel(WRITERS)

for w = 1, WRITERS do
    acked[w] = {}
    fiber.create(function()
        local seq, first_fail = 0, nil
        while not stop do
            seq = seq + 1
            local id = w * 1000000000 + seq
            while not stop do
                local c = leader
                local ok, err = pcall(function()
                    return c.space.ledger:insert({ id, 'writer-' .. w, 1 }, { timeout = 2 })
                end)
                if not ok and tostring(err):find('Duplicate key') then ok = true end
                if ok then
                    table.insert(acked[w], id)
                    sec_ok = sec_ok + 1
                    if first_fail then
                        max_gap = math.max(max_gap, clock.monotonic() - first_fail)
                        first_fail = nil
                    end
                    break
                end
                failed, sec_err = failed + 1, sec_err + 1
                local kind = tostring(err):gsub('%d+', 'N')
                errors_seen[kind] = (errors_seen[kind] or 0) + 1
                first_fail = first_fail or clock.monotonic()
                if c == leader then pcall(find_leader) end
                fiber.sleep(0.05)
            end
        end
        done:put(true)
    end)
end

local t0 = clock.monotonic()
for s = 1, DURATION do
    fiber.sleep(t0 + s - clock.monotonic())
    print(('t=%3ds  leader=%-12s  ok/s=%6d  errors=%d'):format(s, leader_name, sec_ok, sec_err))
    sec_ok, sec_err = 0, 0
end
stop = true
for _ = 1, WRITERS do done:get() end

local total = 0
for w = 1, WRITERS do total = total + #acked[w] end
print(('acknowledged writes: %d (%.0f/s), failed attempts: %d, longest stall per writer: %.2f s'):format(
    total, total / DURATION, failed, max_gap))
for kind, n in pairs(errors_seen) do print(('  error x%d: %s'):format(n, kind)) end

-- every acknowledged id must be on the leader
find_leader()
local missing = 0
for w = 1, WRITERS do
    local ids = acked[w]
    for i = 1, #ids, 5000 do
        local chunk = { unpack(ids, i, math.min(i + 4999, #ids)) }
        missing = missing + leader:eval([[
            local m = 0
            for _, id in ipairs(...) do if box.space.ledger:get(id) == nil then m = m + 1 end end
            return m]], { chunk })
    end
end
-- followers converge on the leader's row count (the restarted one catches up from the WAL)
local counts = {}
local deadline = clock.monotonic() + 30
repeat
    counts = {}
    local want = leader:eval('return box.space.ledger:len()')
    local same = true
    for _, c in ipairs(conns) do
        local ok, n, name = pcall(c.eval, c, 'return box.space.ledger:len(), box.info.name', {}, { timeout = 1 })
        table.insert(counts, ok and (name .. '=' .. n) or 'down')
        if not ok or n ~= want then same = false end
    end
    if same then break end
    fiber.sleep(0.5)
until clock.monotonic() > deadline
print(('lost acknowledged writes: %d; ledger rows per instance: %s'):format(missing, table.concat(counts, ' ')))
os.exit(missing == 0 and 0 or 1)
