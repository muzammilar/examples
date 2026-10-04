-- Transactions in Lua on the server (box.begin/commit/rollback, savepoints, box.atomic),
-- and an interactive transaction from a client over an IPROTO stream (memtx MVCC).
local acc = box.space.accounts
local b2 = acc:get(2).balance

-- explicit transaction + rollback: nothing is kept
box.begin()
acc:update(2, { { '+', 'balance', 1000000 } })
box.rollback()
assert(acc:get(2).balance == b2)

-- savepoint: keep the first change, undo the second
box.begin()
acc:update(2, { { '+', 'balance', 1 } })
local sp = box.savepoint()
acc:update(2, { { '+', 'balance', 1000 } })
box.rollback_to_savepoint(sp)
acc:update(2, { { '-', 'balance', 1 } })
box.commit()
assert(acc:get(2).balance == b2)

-- error inside box.atomic: rolled back, error re-raised
local ok = pcall(box.atomic, function()
    acc:update(2, { { '+', 'balance', 7 } })
    error('abort')
end)
assert(not ok and acc:get(2).balance == b2)

-- Interactive transaction from a client: net.box stream (begin / update / commit are
-- separate requests). Until commit, another connection still reads the old balance.
local netbox = require('net.box')
local c1 = netbox.connect('app:secret@127.0.0.1:3301')
local c2 = netbox.connect('app:secret@127.0.0.1:3301')
local s = c1:new_stream()
s:begin()
s.space.accounts:update(2, { { '+', 'balance', 25 } })
local inside = s.space.accounts:get(2).balance
local outside = c2.space.accounts:get(2).balance
s:commit()
local after = c2.space.accounts:get(2).balance
c1:close() c2:close()
assert(inside == b2 + 25 and outside == b2 and after == b2 + 25)
acc:update(2, { { '-', 'balance', 25 } })

return {
    rollback = 'balance unchanged',
    savepoint = 'second update undone',
    atomic_error = 'rolled back',
    stream_txn = { inside_txn = inside, other_connection_before_commit = outside, after_commit = after },
}
