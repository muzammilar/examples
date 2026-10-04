-- Wallet schema and stored procedures. transfer() is the whole business transaction in one
-- call: idempotency check, balance check, two balance updates, a history row.
local clock = require('clock')

box.once('wallet-v1', function()
    local accounts = box.schema.space.create('accounts', {
        format = { { name = 'id', type = 'unsigned' }, { name = 'balance', type = 'integer' } },
    })
    accounts:create_index('pk', { parts = { 'id' } })
    -- one row per applied transfer; the primary key is the client's request id, which makes
    -- a retried request a no-op
    local transfers = box.schema.space.create('transfers', {
        format = {
            { name = 'req_id', type = 'unsigned' },
            { name = 'from_id', type = 'unsigned' },
            { name = 'to_id', type = 'unsigned' },
            { name = 'amount', type = 'integer' },
            { name = 'ts', type = 'number' },
        },
    })
    transfers:create_index('pk', { parts = { 'req_id' } })
    transfers:create_index('from_id', { parts = { 'from_id', 'req_id' } })
end)

local OK, INSUFFICIENT, DUPLICATE, NO_ACCOUNT = 0, 1, 2, 3

-- read-committed: see transfers that are committed but still waiting for their WAL write.
-- The default (best-effort) aborts read-then-write transactions that overlap a WAL write with
-- "Transaction has been aborted by conflict" (../single-node, Known issues).
function transfer(req_id, from_id, to_id, amount)
    return box.atomic({ txn_isolation = 'read-committed' }, function()
        if box.space.transfers:get(req_id) ~= nil then return DUPLICATE end
        local from = box.space.accounts:get(from_id)
        if from == nil or box.space.accounts:get(to_id) == nil then return NO_ACCOUNT end
        if from.balance < amount then return INSUFFICIENT end
        box.space.accounts:update(from_id, { { '-', 'balance', amount } })
        box.space.accounts:update(to_id, { { '+', 'balance', amount } })
        box.space.transfers:insert({ req_id, from_id, to_id, amount, clock.time() })
        return OK
    end)
end

-- empty both spaces and create accounts 1..n with `balance` each
function reset(n, balance)
    box.space.transfers:truncate()
    box.space.accounts:truncate()
    for first = 1, n, 10000 do
        box.atomic(function()
            for id = first, math.min(first + 9999, n) do
                box.space.accounts:insert({ id, balance })
            end
        end)
    end
    return box.space.accounts:len()
end

-- sum and minimum of all balances, number of transfers
function audit()
    local sum, min = 0, math.huge
    for _, t in box.space.accounts:pairs() do
        sum = sum + t.balance
        if t.balance < min then min = t.balance end
    end
    return { sum = sum, min = min, transfers = box.space.transfers:len() }
end
