-- Stored procedures from app/init.lua. transfer(): one transaction on the server (2 balance
-- updates, a counter bump, a history insert); failing calls change nothing. archive(): moves
-- old transfers from memtx to the vinyl space; history() reads both.
local before = total_balance()
local ids = {}
for i = 1, 10 do
    table.insert(ids, transfer(1, 2 + i, 50))
end
local ok, err = pcall(transfer, 1, 2, 10000)      -- account 1 has 500 left
assert(not ok and tostring(err):find('insufficient funds'), tostring(err))
local ok2, err2 = pcall(transfer, 1, 999999, 1)    -- missing account
assert(not ok2 and tostring(err2):find('no such account'), tostring(err2))

local a1 = box.space.accounts:get(1)
assert(a1.balance == 500, 'account 1 should hold 1000 - 10 * 50')
assert(total_balance() == before, 'total balance changed')
assert(box.space.transfers:count() == 10, 'failed transfers must not leave history rows')
local moved = archive(8)
assert(moved == 8 and box.space.transfers:count() == 2 and box.space.transfers_archive:count() == 8)
local last = history(1, 3)
return {
    transfer_ids = ids,
    rejected = { tostring(err), tostring(err2) },
    account_1_balance = a1.balance,
    total_before = before,
    total_after = total_balance(),
    archived = { moved = moved, memtx = box.space.transfers:count(), vinyl = box.space.transfers_archive:count() },
    history_1_last3 = { last[1]:totable(), last[2]:totable(), last[3]:totable() },
}
