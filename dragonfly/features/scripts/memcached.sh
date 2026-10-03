# memcached text protocol on port 11211; it shares the keyspace with the Redis API.
# busybox nc stops at stdin EOF, sometimes before the reply arrives: keep stdin open for a second.
mc() { { printf "$1"; sleep 1; } | nc dragonfly 11211 | tr -d '\r'; }

out=$(mc 'set page:home 0 0 5\r\nhello\r\nget page:home\r\nset visits 0 0 1\r\n0\r\nincr visits 5\r\nincr visits 1\r\n')
echo "$out"
[ "$out" = "$(printf 'STORED\nVALUE page:home 0 5\nhello\nEND\nSTORED\n5\n6')" ] ||
	{ echo "FAIL: unexpected memcached replies"; exit 1; }

c() { valkey-cli -h dragonfly "$@"; }
echo "redis GET page:home -> $(c GET page:home), GET visits -> $(c GET visits)"
[ "$(c GET visits)" = 6 ] || { echo "FAIL: redis does not see the memcached value"; exit 1; }
c SET page:about 'set via redis' > /dev/null
out=$(mc 'get page:about\r\n')
echo "$out"
echo "$out" | grep -q '^set via redis$' || { echo "FAIL: memcached does not see the redis value"; exit 1; }
