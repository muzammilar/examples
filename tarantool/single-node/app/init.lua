-- Loaded by `app.file` on every start, after box is configured. Everything is idempotent:
-- on a restart the spaces already exist (recovered from snapshot + WAL) and only the
-- functions are defined again (Lua functions live in memory, not in the database).
local log = require('log')
local clock = require('clock')

local transfer_format = {
    { name = 'id', type = 'unsigned' },
    { name = 'from_id', type = 'unsigned' },
    { name = 'to_id', type = 'unsigned' },
    { name = 'amount', type = 'integer' },
    { name = 'ts', type = 'number' },
}

box.once('schema-v1', function()
    -- memtx: all data in RAM, persisted by the WAL and periodic snapshots
    local accounts = box.schema.space.create('accounts', {
        engine = 'memtx',
        format = {
            { name = 'id', type = 'unsigned' },
            { name = 'owner', type = 'string' },
            { name = 'balance', type = 'integer' },
            { name = 'updated_at', type = 'number' },
        },
    })
    accounts:create_index('pk', { type = 'TREE', parts = { 'id' } })
    accounts:create_index('owner', { type = 'TREE', parts = { 'owner' }, unique = false })

    -- memtx HASH index: point lookups only, no ranges
    local sessions = box.schema.space.create('sessions', {
        engine = 'memtx',
        format = { { name = 'token', type = 'string' }, { name = 'account_id', type = 'unsigned' } },
    })
    sessions:create_index('pk', { type = 'HASH', parts = { 'token' } })

    -- recent transfers in memtx; archive() moves old ones to the vinyl space below
    local transfers = box.schema.space.create('transfers', { engine = 'memtx', format = transfer_format })
    transfers:create_index('pk', { parts = { 'id' } })
    transfers:create_index('from_ts', { parts = { 'from_id', 'ts', 'id' } })

    -- vinyl: LSM tree on disk, for data larger than RAM (the transfer archive)
    local archive = box.schema.space.create('transfers_archive', { engine = 'vinyl', format = transfer_format })
    archive:create_index('pk', { parts = { 'id' } })
    archive:create_index('from_ts', { parts = { 'from_id', 'ts', 'id' } })

    -- id generator, a memtx row so transfer() needs nothing else
    local counters = box.schema.space.create('counters', {
        format = { { name = 'name', type = 'string' }, { name = 'value', type = 'unsigned' } },
    })
    counters:create_index('pk', { parts = { 'name' } })
    counters:insert({ 'transfer_id', 0 })
    log.info('schema-v1 created')
end)

-- Stored procedure: one call does the whole transfer as one transaction on the server.
-- Errors raised inside box.atomic roll everything back. It touches memtx only, so it never
-- yields: transactions run one after another on the TX thread and cannot conflict.
function transfer(from_id, to_id, amount)
    if amount <= 0 then
        box.error({ reason = 'amount must be positive', code = 1001 })
    end
    return box.atomic({ txn_isolation = 'read-committed' }, function()
        local from = box.space.accounts:get(from_id)
        local to = box.space.accounts:get(to_id)
        if from == nil or to == nil then
            box.error({ reason = 'no such account', code = 1002 })
        end
        if from.balance < amount then
            box.error({ reason = 'insufficient funds', code = 1003 })
        end
        local now = clock.time()
        box.space.accounts:update(from_id, { { '-', 'balance', amount }, { '=', 'updated_at', now } })
        box.space.accounts:update(to_id, { { '+', 'balance', amount }, { '=', 'updated_at', now } })
        local id = box.space.counters:update('transfer_id', { { '+', 'value', 1 } }).value
        box.space.transfers:insert({ id, from_id, to_id, amount, now })
        return id
    end)
end

-- Move transfers with id <= up_to from memtx to vinyl, batch rows per transaction.
-- Vinyl statements can yield (disk reads for the unique checks), so these transactions run
-- concurrently with others under MVCC; they only touch old rows, which nothing else writes.
function archive(up_to, batch)
    batch = batch or 1000
    local moved = 0
    while true do
        local rows = box.space.transfers:select({ up_to }, { iterator = 'LE', limit = batch })
        if #rows == 0 then return moved end
        box.atomic(function()
            for _, t in ipairs(rows) do
                box.space.transfers_archive:replace(t)
                box.space.transfers:delete(t.id)
            end
        end)
        moved = moved + #rows
    end
end

-- Sum of all balances: transfers move money, so it never changes.
function total_balance()
    local sum = 0
    for _, t in box.space.accounts:pairs() do
        sum = sum + t.balance
    end
    return sum
end

-- Last n transfers out of an account, newest first: memtx first, then the vinyl archive.
function history(account_id, n)
    local out = {}
    for _, space in ipairs({ box.space.transfers, box.space.transfers_archive }) do
        for _, t in space.index.from_ts:pairs({ account_id }, { iterator = 'REQ' }) do
            if #out >= n then return out end
            table.insert(out, t)
        end
    end
    return out
end
