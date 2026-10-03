# RedisJSON-compatible commands, built in
c() { valkey-cli -h dragonfly "$@"; }

c JSON.SET user:1 '$' '{"name":"ann","age":31,"tags":["admin"],"address":{"city":"Oslo"}}'
echo "JSON.GET \$.address.city -> $(c JSON.GET user:1 '$.address.city')"
echo "JSON.NUMINCRBY \$.age 1 -> $(c JSON.NUMINCRBY user:1 '$.age' 1)"
echo "JSON.ARRAPPEND \$.tags \"dev\" -> $(c JSON.ARRAPPEND user:1 '$.tags' '"dev"')"
doc=$(c JSON.GET user:1)
echo "JSON.GET user:1 -> $doc"
[ "$doc" = '{"address":{"city":"Oslo"},"age":32,"name":"ann","tags":["admin","dev"]}' ] ||
	{ echo "FAIL: unexpected document"; exit 1; }
