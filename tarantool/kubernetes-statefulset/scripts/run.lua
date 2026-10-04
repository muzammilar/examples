-- Runs scripts/NN-*.lua on the server through net.box `eval` and prints what each returns.
-- NN-*.lua runs on the current leader (the only writable instance); NN-*.all.lua on every
-- instance. Any error exits non-zero, so `make test` fails.
local netbox = require('net.box')
local fio = require('fio')

local uris = {}
for u in (os.getenv('TARANTOOL_URIS') or ''):gmatch('[^,]+') do table.insert(uris, u) end

local function dump(v, indent)
    indent = indent or ''
    if type(v) ~= 'table' then return tostring(v) end
    local n, flat = 0, true
    for k, x in pairs(v) do
        n = n + 1
        if type(k) ~= 'number' or type(x) == 'table' then flat = false end
    end
    if n == #v and (flat or n == 0) then
        local parts = {}
        for _, x in ipairs(v) do table.insert(parts, tostring(x)) end
        return '[' .. table.concat(parts, ', ') .. ']'
    end
    local keys = {}
    for k in pairs(v) do table.insert(keys, k) end
    table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
    local lines = {}
    for _, k in ipairs(keys) do
        table.insert(lines, ('\n%s  %s: %s'):format(indent, tostring(k), dump(v[k], indent .. '  ')))
    end
    return table.concat(lines)
end

local conns, leader = {}, nil
for _, u in ipairs(uris) do
    local c = netbox.connect(u, { wait_connected = 10 })
    if c:is_connected() then
        table.insert(conns, c)
        if not c:eval('return box.info.ro') then leader = c end
    else
        print('cannot connect to ' .. u .. ': ' .. tostring(c.error))
    end
end
if leader == nil then print('no writable instance (no leader elected?)') os.exit(1) end

for _, path in ipairs(arg) do
    local f = assert(fio.open(path, { 'O_RDONLY' }))
    local src = f:read()
    f:close()
    local targets = path:find('%.all%.lua$') and conns or { leader }
    for _, c in ipairs(targets) do
        local name = c:eval('return box.info.name')
        print(('==== %s on %s'):format(path, name))
        local ok, res = pcall(function() return { c:eval(src) } end)
        if not ok then
            print('FAILED: ' .. tostring(res))
            os.exit(1)
        end
        print((dump(res[1]):gsub('^\n', '')))
    end
end
os.exit(0) -- flushes stdout; open connections would keep the event loop running
