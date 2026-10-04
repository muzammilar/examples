-- WAL + snapshot: 10,000 rows into a memtx and a vinyl space, box.snapshot(), then 5,000
-- more rows that exist only in the WAL. `make test` then kills the container (SIGKILL)
-- and 06-after-restart.lua checks all 15,000 came back.
for _, e in ipairs({ 'memtx', 'vinyl' }) do
    local name = 'restart_' .. e
    if box.space[name] then box.space[name]:drop() end
    box.schema.space.create(name, { engine = e })
    box.space[name]:create_index('pk')
end
local function load(from, to)
    box.atomic(function()
        for i = from, to do
            box.space.restart_memtx:insert({ i, 'row ' .. i })
            box.space.restart_vinyl:insert({ i, 'row ' .. i })
        end
    end)
end
load(1, 10000)
box.snapshot()
load(10001, 15000)
local fio = require('fio')
local dir = box.cfg.memtx_dir
local files = {}
for _, p in ipairs(fio.glob(fio.pathjoin(dir, '*.snap'))) do table.insert(files, fio.basename(p)) end
for _, p in ipairs(fio.glob(fio.pathjoin(box.cfg.wal_dir, '*.xlog'))) do table.insert(files, fio.basename(p)) end
table.sort(files)
return {
    rows = { memtx = box.space.restart_memtx:len(), vinyl = box.space.restart_vinyl:count() },
    lsn = box.info.lsn,
    data_dir = dir,
    files = files,
}
