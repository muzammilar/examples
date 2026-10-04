-- On the leader: 1,000 rows into the synchronous space `ledger`. Each commit waits until 2 of 3
-- instances have it in their WAL (box.info.synchro shows the limbo queue and quorum).
box.space.ledger:truncate()
for i = 1, 1000 do
    box.space.ledger:insert({ i, 'test', i })
end
local sum = 0
for _, t in box.space.ledger:pairs() do sum = sum + t.amount end
assert(box.space.ledger:len() == 1000 and sum == 500500)
return {
    ledger_rows = box.space.ledger:len(),
    ledger_sum = sum,
    is_sync = box.space.ledger.is_sync,
    synchro = { quorum = box.info.synchro.quorum, queue_len = box.info.synchro.queue.len,
                queue_owner = box.info.synchro.queue.owner },
}
