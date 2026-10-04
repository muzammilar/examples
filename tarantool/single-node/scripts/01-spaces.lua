-- Spaces created by app/init.lua: engine, indexes, row count.
local out = {}
for _, name in ipairs({ 'accounts', 'sessions', 'transfers', 'transfers_archive' }) do
    local s = box.space[name]
    assert(s, 'space ' .. name .. ' missing: app/init.lua did not run')
    local idx = {}
    for i = 0, #s.index do
        local ix = s.index[i]
        local parts = {}
        for _, p in ipairs(ix.parts) do table.insert(parts, p.fieldno .. ':' .. p.type) end
        table.insert(idx, ('%s %s unique=%s parts=%s'):format(ix.name, ix.type, tostring(ix.unique), table.concat(parts, ',')))
    end
    out[name] = { engine = s.engine, indexes = idx }
end
out.version = box.info.version
out.memtx_mvcc = box.cfg.memtx_use_mvcc_engine
return out
