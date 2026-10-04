-- `make test`: 10,000 puts through the router, read them back, show the bucket distribution.
local netbox = require('net.box')
local router = netbox.connect(os.getenv('ROUTER_URI') or 'app:secret@router-a-001:3301', { wait_connected = 10 })
assert(router:is_connected(), tostring(router.error))
for i = 1, 10000 do router:call('put', { i, 'value-' .. i }) end
for i = 1, 10000, 997 do
    local v = router:call('get', { i })
    assert(v == 'value-' .. i, ('get(%d) = %s'):format(i, tostring(v)))
end
local missing = 0
local ids = {}
for i = 1, 10000 do ids[i] = i end
missing = router:call('check', { ids })
assert(missing == 0, missing .. ' ids missing')
print('10000 puts, sampled gets ok, missing: 0')
for name, b in pairs(router:call('buckets')) do
    print(('%-10s active buckets=%4d  rows=%d'):format(name, b.active, b.rows))
end
os.exit(0)
