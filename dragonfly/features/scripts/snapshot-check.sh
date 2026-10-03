# after the restart: Dragonfly loaded the newest snapshot from /data on startup
c() { valkey-cli -h dragonfly "$@"; }

echo "keys after restart: $(c DBSIZE)"
s=$(c GET snap:string); h=$(c HGET snap:hash b); j=$(c JSON.GET snap:json)
echo "GET snap:string -> $s, HGET snap:hash b -> $h, JSON.GET snap:json -> $j"
[ "$s" = 'still here' ] && [ "$h" = 2 ] && [ "$j" = '{"saved":true}' ] ||
	{ echo "FAIL: data did not survive the restart"; exit 1; }
