-- Runs each scripts/NN-*.lua on the server through net.box `eval` and prints what it returns
-- as an indented key: value list. Any error (failed assert, box.error) exits non-zero, so `make test` fails.
--   tarantool scripts/run.lua scripts/01-*.lua ...
local netbox = require('net.box')
local fio = require('fio')

local uri = os.getenv('TARANTOOL_URI') or 'app:secret@tarantool:3301'
local conn = netbox.connect(uri, { wait_connected = 10 })
if not conn:is_connected() then
    print('cannot connect to ' .. uri .. ': ' .. tostring(conn.error))
    os.exit(1)
end
-- indented key: value dump, keys sorted, short arrays on one line
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

for _, path in ipairs(arg) do
    local f = assert(fio.open(path, { 'O_RDONLY' }))
    local src = f:read()
    f:close()
    print('==== ' .. path)
    local ok, res = pcall(function() return { conn:eval(src) } end)
    if not ok then
        print('FAILED: ' .. tostring(res))
        os.exit(1)
    end
    print((dump(res[1]):gsub('^\n', '')))
end
conn:close()
