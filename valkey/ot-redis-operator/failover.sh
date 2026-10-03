#!/bin/sh
# Stop a master and check its replica takes over. HARD=1 freezes valkey-server
# with SIGSTOP first, so the preStop hook (CLUSTER FAILOVER) never runs.
set -e
k() { kubectl --context "kind-$CLUSTER_NAME" -n "$NAMESPACE" "$@"; }
cli() { pod=$1; shift; k exec "$pod" -c "${pod%-*}" -- valkey-cli "$@"; }
pods=$(k get pods -l 'app in (valkey-leader,valkey-follower)' -o jsonpath='{.items[*].metadata.name}')

for p in $pods; do [ "$(cli $p role | head -1)" = master ] && victim=$p && break; done
for p in $pods; do [ $p != $victim ] && client=$p && break; done
vid=$(cli $victim cluster myid)
replica_ip=$(cli $victim info replication | tr -d '\r' | sed -n 's/^slave0:ip=\([^,]*\),.*/\1/p')
replica=$(k get pods --field-selector status.podIP=$replica_ip -o jsonpath='{.items[0].metadata.name}')
rid=$(cli $replica cluster myid)
echo "master $victim, replica $replica"

for i in $(seq 30); do cli $client -c set failover:$i v$i >/dev/null; done

if [ -n "$HARD" ]; then
	node=$CLUSTER_NAME-control-plane
	ctr=$(docker exec $node crictl ps -q --label io.kubernetes.pod.namespace=$NAMESPACE --label io.kubernetes.pod.name=$victim)
	pid=$(docker exec $node crictl inspect --output go-template --template '{{.info.pid}}' $ctr)
	echo "SIGSTOP valkey-server in $victim"
	docker exec $node kill -STOP $pid
else
	k delete pod $victim --wait=false
fi

for i in $(seq 60); do
	[ "$(cli $replica role 2>/dev/null | head -1)" = master ] &&
		cli $client cluster info | grep -q cluster_state:ok && break
	[ "$i" -lt 60 ] || { echo "$replica not promoted after 120 s"; exit 1; }
	sleep 2
done
echo "$replica promoted after ~$((i * 2)) s"

for i in $(seq 30); do
	[ "$(cli $client -c get failover:$i)" = v$i ] || { echo "failover:$i missing"; exit 1; }
done
echo "30/30 keys read back"

[ -z "$HARD" ] || k delete pod $victim --grace-period=0 --force

# the recreated pod keeps its node ID (nodes.conf PVC) and rejoins as a replica
for i in $(seq 120); do
	cli $client cluster nodes | awk -v v=$vid -v r=$rid '$1 == v && $3 !~ /fail/ && $4 == r && $8 == "connected"' | grep -q . && break
	[ "$i" -lt 120 ] || { echo "$victim did not rejoin after 600 s"; exit 1; }
	sleep 5
done
echo "$victim rejoined as a replica of $replica"
k wait pod/$victim --for=condition=Ready --timeout=5m
k wait rediscluster/valkey --for=jsonpath='{.status.state}'=Ready --timeout=5m
cli $client cluster nodes | sort -k3
