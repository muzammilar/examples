-- Reset demo data, load 1,000 accounts, then reads through each index type.
box.space.accounts:truncate()
box.space.sessions:truncate()
box.space.transfers:truncate()
box.space.transfers_archive:truncate()
box.space.counters:replace({ 'transfer_id', 0 })

local now = require('clock').time()
box.atomic(function()
    for i = 1, 1000 do
        box.space.accounts:insert({ i, 'owner-' .. (i % 100), 1000, now })
    end
end)
box.space.sessions:insert({ 'tok-abc', 42 })

local by_pk = box.space.accounts:get(42)
local by_owner = box.space.accounts.index.owner:select({ 'owner-7' }, { limit = 3 })
local range = box.space.accounts:select({ 995 }, { iterator = 'GE' })
local upsert_rows = 0
-- upsert: insert if missing, else apply the update ops (no read on the client)
box.space.accounts:upsert({ 1001, 'owner-new', 0, now }, { { '+', 'balance', 5 } })
box.space.accounts:upsert({ 1001, 'owner-new', 0, now }, { { '+', 'balance', 5 } })
local upserted = box.space.accounts:get(1001)
assert(upserted.balance == 5, 'upsert should have added 5 to the inserted 0')
box.space.accounts:delete(1001)

assert(box.space.accounts:len() == 1000)
assert(#by_owner == 3 and by_owner[1].owner == 'owner-7')
assert(#range == 6 and range[1].id == 995)
return {
    accounts = box.space.accounts:len(),
    get_42 = by_pk:totable(),
    owner_7_first3 = { by_owner[1].id, by_owner[2].id, by_owner[3].id },
    ge_995 = #range,
    session_tok_abc = box.space.sessions:get('tok-abc'):totable(),
    upsert_twice_balance = upserted.balance,
}
