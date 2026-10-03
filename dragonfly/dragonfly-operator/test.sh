#!/bin/sh
# Writes go through the dragonfly Service, which selects the pod labelled role=master.
set -e
k() { kubectl --context "kind-$CLUSTER_NAME" -n "$NAMESPACE" "$@"; }
cli() { pod=$1; shift; k exec "$pod" -c dragonfly -- redis-cli "$@" | tr -d '\r'; }
master=$(k get pods -l app=dragonfly,role=master -o jsonpath='{.items[0].metadata.name}')
replicas=$(k get pods -l app=dragonfly,role=replica -o jsonpath='{.items[*].metadata.name}')
echo "master $master, replicas $replicas"
cli $master info replication | grep -E '^(role|connected_slaves|slave[0-9])'

k exec $master -c dragonfly -- sh -c \
	'for i in $(seq 100); do redis-cli -h dragonfly set test:$i v$i >/dev/null; done'
[ "$(cli $master -h dragonfly get test:42)" = v42 ]
echo "100 keys written via the Service"

sleep 1
for r in $replicas; do
	[ "$(cli $r get test:100)" = v100 ] || { echo "$r is missing test:100"; exit 1; }
	out=$(cli $r set test:1 nope 2>&1 || true)
	echo "$out" | grep -q READONLY || { echo "$r accepted a write: $out"; exit 1; }
	echo "$r has the keys and rejects writes: $out"
done

[ "$(cli $master -h dragonfly eval "return redis.call('incrby', KEYS[1], ARGV[1])" 1 test:counter 5)" -ge 5 ]
cli $master -h dragonfly eval "return redis.call('get', KEYS[1]) .. '+lua'" 1 test:7
