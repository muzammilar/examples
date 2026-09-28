-- wrk script: POST a RonDB REST API pk-read of a random row of sbtest.sbtest1
-- (ids 1..TABLE_SIZE, as loaded by `sysbench prepare`) per request. wrk
-- reports any answer other than 2xx/3xx as "Non-2xx or 3xx responses".
local rows = tonumber(os.getenv("TABLE_SIZE"))

wrk.method = "POST"
wrk.headers["Content-Type"] = "application/json"

request = function()
   local body = string.format(
      '{"filters": [{"column": "id", "value": %d}], "readColumns": [{"column": "k"}, {"column": "c"}]}',
      math.random(1, rows))
   return wrk.format(nil, nil, nil, body)
end

