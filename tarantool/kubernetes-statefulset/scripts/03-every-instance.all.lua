-- On every instance: same rows; followers are read-only and refuse writes.
local fiber = require('fiber')
local deadline = fiber.clock() + 5
while box.space.ledger:len() < 1000 and fiber.clock() < deadline do fiber.sleep(0.05) end
local rows = box.space.ledger:len()
assert(rows == 1000, 'ledger rows: ' .. rows)
local write = 'ok (leader)'
if box.info.ro then
    local ok, err = pcall(box.space.ledger.insert, box.space.ledger, { 999999, 'x', 0 })
    assert(not ok, 'a follower accepted a write')
    write = tostring(err)
end
return { name = box.info.name, state = box.info.election.state, ro = box.info.ro, ledger_rows = rows, write = write }
