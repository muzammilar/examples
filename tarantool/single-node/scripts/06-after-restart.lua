-- After `docker kill`: memtx rows come back from the last .snap plus a replay of the .xlog
-- written after it; vinyl rows from its run files plus the same WAL replay.
local m = box.space.restart_memtx:len()
local v = box.space.restart_vinyl:count()
assert(m == 15000, 'memtx rows after restart: ' .. m)
assert(v == 15000, 'vinyl rows after restart: ' .. v)
assert(box.space.accounts:len() == 1000)
assert(total_balance() == 1000 * 1000, 'total balance changed across the restart')
assert(box.space.transfers:count() + box.space.transfers_archive:count() == 10)
local r = box.space.restart_memtx:get(15000)
box.space.restart_memtx:drop()
box.space.restart_vinyl:drop()
return {
    rows = { memtx = m, vinyl = v },
    last_row = r:totable(),
    accounts = box.space.accounts:len(),
    total_balance = total_balance(),
    uptime_s = box.info.uptime,
    lsn = box.info.lsn,
}
