#!/bin/sh
# 1. delete every pod: Dragonfly saves a snapshot on SIGTERM, keys come back.
# 2. SIGKILL every dragonfly process: only the last cron snapshot survives.
set -e
k() { kubectl --context "kind-$CLUSTER_NAME" -n "$NAMESPACE" "$@"; }
master() { k get pods -l app=dragonfly,role=master -o jsonpath='{.items[0].metadata.name}'; }
write() { k exec $(master) -c dragonfly -- sh -c "for i in \$(seq $2); do echo SET $1:\$i v\$i; done | redis-cli -h dragonfly >/dev/null"; }
count() {
	for p in $(k get pods -l app=dragonfly -o jsonpath='{.items[*].metadata.name}'); do
		n=$(k exec $p -c dragonfly -- sh -c "for i in \$(seq $2); do echo GET $1:\$i; done | redis-cli" | grep -c '^v' || true)
		echo "$p: $n/$2 $1 keys"
		[ "$n" = "$3" ]
	done
}

write deleted 1000
echo "1000 keys written, deleting all pods"
k delete pods -l app=dragonfly
./wait-ready.sh
k logs $(master) -c dragonfly | grep 'Load finished'
count deleted 1000 1000

write killed 1000
written=$(date +%s)
echo "1000 keys written, waiting for the next cron snapshot"
for p in $(k get pods -l app=dragonfly -o jsonpath='{.items[*].metadata.name}'); do
	for i in $(seq 40); do
		[ "$(k exec $p -c dragonfly -- stat -c %Y /dragonfly/snapshots/dump-summary.dfs)" -gt "$written" ] && break
		[ "$i" -lt 40 ] || { echo "no snapshot on $p after 120 s"; exit 1; }
		sleep 3
	done
done
write lost 100
echo "snapshot taken; 100 more keys written, SIGKILL every dragonfly process"
restarts() { k get pods -l app=dragonfly -o jsonpath='{.items[*].status.containerStatuses[0].restartCount}' | awk '{ for (i = 1; i <= NF; i++) s += $i; print s }'; }
before=$(restarts)
docker exec $CLUSTER_NAME-control-plane pkill -KILL -x dragonfly
until [ "$(restarts)" -ge $((before + 3)) ]; do sleep 1; done
./wait-ready.sh
k get pods -l app=dragonfly -L role
count killed 1000 1000
count lost 100 0
