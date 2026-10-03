#!/bin/sh
# Delete the master pod and time how long until a replica is master and the
# Service points at it.
set -e
k() { kubectl --context "kind-$CLUSTER_NAME" -n "$NAMESPACE" "$@"; }
cli() { pod=$1; shift; k exec "$pod" -c dragonfly -- timeout 2 redis-cli "$@" 2>/dev/null | tr -d '\r'; }
old=$(k get pods -l app=dragonfly,role=master -o jsonpath='{.items[0].metadata.name}')
replicas=$(k get pods -l app=dragonfly,role=replica -o jsonpath='{.items[*].metadata.name}')
echo "master $old, replicas $replicas"

k exec $old -c dragonfly -- sh -c \
	'for i in $(seq 1000); do echo "SET failover:$i v$i"; done | redis-cli -h dragonfly >/dev/null'
echo "1000 keys written"

start=$(date +%s)
k delete pod $old --wait=false
new=
for i in $(seq 120); do
	for r in $replicas; do
		[ "$(cli $r role | head -1)" = master ] || continue
		[ "$(k get endpointslices -l kubernetes.io/service-name=dragonfly -o jsonpath='{.items[*].endpoints[*].addresses[*]}')" = \
			"$(k get pod $r -o jsonpath='{.status.podIP}')" ] && new=$r
	done
	[ -n "$new" ] && break
	[ "$i" -lt 120 ] || { echo "no replica promoted after 120 s"; exit 1; }
	sleep 1
done
echo "$new is master behind the Service after $(($(date +%s) - start)) s"
# kube-proxy lags behind the EndpointSlice; a connect in that window goes to the
# dead pod and hangs in SYN retries, hence the 2 s timeout per try
for i in $(seq 30); do
	[ "$(cli $new -h dragonfly set failover:after ok)" = OK ] && break
	[ "$i" -lt 30 ] || { echo "no write through the Service after 30 tries"; exit 1; }
	sleep 1
done
echo "writes through the Service work after $(($(date +%s) - start)) s"

n=$(k exec $new -c dragonfly -- sh -c 'for i in $(seq 1000); do echo "GET failover:$i"; done | redis-cli -h dragonfly' | grep -c '^v')
echo "$n/1000 keys read via the Service"
[ "$n" = 1000 ]

./wait-ready.sh 300
[ "$(k get pod $old -o jsonpath='{.metadata.labels.role}')" = replica ]
echo "$old is back as a replica of $new"
cli $old info replication | grep -E '^(role|master_host|master_link_status)'
[ "$(cli $old get failover:after)" = ok ]
