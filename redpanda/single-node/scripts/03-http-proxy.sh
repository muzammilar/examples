#!/bin/bash
# HTTP Proxy (pandaproxy, port 8082): produce and consume over REST, no Kafka client.
set -euo pipefail
P=http://localhost:8082
J='Content-Type: application/vnd.kafka.json.v2+json'
set -x
rpk topic delete clicks >/dev/null 2>&1 || true
rpk topic create clicks --partitions 1 --replicas 1
curl -sf "$P/topics" ; echo
curl -sf -X POST "$P/topics/clicks" -H "$J" \
	-d '{"records":[{"key":"u1","value":{"page":"/"}},{"key":"u2","value":{"page":"/cart"}},{"key":"u1","value":{"page":"/pay"}}]}'
echo
# consumer instance in group web, subscribe, fetch (first fetch can be empty while the group joins)
base=$(curl -sf -X POST "$P/consumers/web" -H 'Content-Type: application/vnd.kafka.v2+json' \
	-d '{"name":"c1","format":"json","auto.offset.reset":"earliest"}' | grep -o '"base_uri":"[^"]*"' | cut -d'"' -f4)
base=${base:-$P/consumers/web/instances/c1}
base=$P${base#*:8082}
curl -sf -X POST "$base/subscription" -H 'Content-Type: application/vnd.kafka.v2+json' -d '{"topics":["clicks"]}'
n=0
for i in $(seq 10); do
	out=$(curl -sf "$base/records?timeout=1000&max_bytes=100000" -H 'Accept: application/vnd.kafka.json.v2+json')
	n=$(echo "$out" | grep -o '"offset"' | wc -l)
	[ "$n" -ge 3 ] && break
	sleep 1
done
echo "$out"
curl -sf -X DELETE "$base" -H 'Content-Type: application/vnd.kafka.v2+json'
set +x
[ "$n" = 3 ] || { echo "FAIL: expected 3 records over HTTP, got $n"; exit 1; }
echo "OK: 3 records produced and consumed over HTTP"
