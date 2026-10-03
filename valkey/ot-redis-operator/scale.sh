#!/bin/sh
# ./scale.sh up|down [PRIMARIES]
set -e
k() { kubectl --context "kind-$CLUSTER_NAME" -n "$NAMESPACE" "$@"; }
cli() { k exec valkey-leader-0 -c valkey-leader -- valkey-cli "$@"; }

cur=$(k get rediscluster valkey -o jsonpath='{.spec.clusterSize}')
if [ "$1" = up ]; then n=${2:-$((cur + 1))}; else n=${2:-$((cur - 1))}; fi
[ "$n" -ge 3 ] && [ "$n" != "$cur" ] || { echo "bad PRIMARIES=$n (clusterSize is $cur, minimum 3)"; exit 1; }

k exec valkey-leader-0 -c valkey-leader -- sh -c \
	'for i in $(seq 100); do valkey-cli -c set scale:$i v$i >/dev/null; done'
cli --cluster info localhost:6379 | grep -- '->'

echo "clusterSize $cur -> $n"
k patch rediscluster valkey --type merge -p "{\"spec\":{\"clusterSize\":$n}}"

# Settled: Ready, 2n healthy nodes, n masters owning slots, no open slots.
# The operator's rebalance after add-node often stalls on CLUSTERDOWN and
# leaves slots open; after 30 s of that, fix and rebalance ourselves.
open=0
for i in $(seq 120); do
	sleep 5
	state=$(k get rediscluster valkey -o jsonpath='{.status.state} {.status.readyLeaderReplicas} {.status.readyFollowerReplicas}')
	nodes=$(cli cluster nodes | grep -v fail || true)
	total=$(echo "$nodes" | grep -c . || true)
	owners=$(echo "$nodes" | awk '/master/ && NF > 8' | wc -l | tr -d ' ')
	if echo "$nodes" | grep -q '\['; then open=$((open + 1)); else open=0; fi
	echo "$((i * 5)) s: $state, $total nodes, $owners masters with slots, open-slot polls $open"
	[ "$state" = "Ready $n $n" ] && [ "$total" = $((2 * n)) ] && [ "$owners" = "$n" ] && [ "$open" = 0 ] && break
	if [ "$open" -ge 6 ]; then
		echo "rebalance stalled, running --cluster fix + rebalance"
		cli --cluster fix localhost:6379 --cluster-yes | grep -E '^(>>> Fix|\[ERR)' || true
		cli --cluster rebalance localhost:6379 --cluster-use-empty-masters | grep -E '^(Moving|\[ERR)' || true
		open=0
	fi
	[ "$i" -lt 120 ] || { echo "not settled after 600 s"; exit 1; }
done

# the operator keeps PVCs of removed pods; reusing them breaks a later scale-up
if [ "$1" = down ]; then
	for pvc in $(k get pvc -o name | awk -F- -v n="$n" '$NF + 0 >= n'); do k delete "$pvc" --wait=false; done
fi

cli --cluster info localhost:6379 | grep -- '->'
k exec valkey-leader-0 -c valkey-leader -- sh -c \
	'for i in $(seq 100); do [ "$(valkey-cli -c get scale:$i)" = v$i ] || { echo "scale:$i missing"; exit 1; }; done'
echo "100/100 keys read back"
