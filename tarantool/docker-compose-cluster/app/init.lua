-- Loaded on every instance. Schema changes are only allowed on the leader (the others are
-- read-only), so the schema is created whenever this instance becomes writable; replicas get
-- it through replication. if_not_exists makes it a no-op afterwards.
local fiber = require('fiber')
local log = require('log')

local function create_schema()
    -- synchronous: a commit returns after a quorum (2 of 3) has the transaction
    local ledger = box.schema.space.create('ledger', {
        is_sync = true,
        if_not_exists = true,
        format = {
            { name = 'id', type = 'unsigned' },
            { name = 'writer', type = 'string' },
            { name = 'amount', type = 'integer' },
        },
    })
    ledger:create_index('pk', { if_not_exists = true })
    -- asynchronous: commit returns after the local WAL write (benchmark comparison)
    local kv = box.schema.space.create('kv_async', { if_not_exists = true })
    kv:create_index('pk', { if_not_exists = true })
    local kvs = box.schema.space.create('kv_sync', { is_sync = true, if_not_exists = true })
    kvs:create_index('pk', { if_not_exists = true })
end

box.watch('box.status', function(_, status)
    if not status.is_ro then
        fiber.create(function()
            local ok, err = pcall(create_schema)
            if not ok then log.warn('create_schema: %s', err) end
        end)
    end
end)

-- one line for `make status`: name, election state, term, leader, replicated position, rows
function cluster_status()
    local leader = 'none'
    for _, r in pairs(box.info.replication) do
        if r.id == box.info.election.leader then leader = r.name end
    end
    -- vclock entry 0 counts local-only rows; the rest is the replicated position
    local pos = 0
    for id, lsn in pairs(box.info.vclock) do if id ~= 0 then pos = pos + lsn end end
    return ('%s %-9s term=%d leader=%s vclock_sum=%d ledger=%d'):format(box.info.name,
        box.info.election.state, box.info.election.term, leader, pos,
        box.space.ledger and box.space.ledger:len() or 0)
end
