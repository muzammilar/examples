#!/bin/sh
# Wait until Dragonfly itself matches the spec: one master with replicas-1
# online replicas, each pod labelled with its role, the Service on the master.
# status.phase is Ready as soon as the first pod is up, so it is not enough.
set -e
k() { kubectl --context "kind-$CLUSTER_NAME" -n "$NAMESPACE" "$@"; }
cli() { pod=$1; shift; k exec "$pod" -c dragonfly -- redis-cli "$@" 2>/dev/null | tr -d '\r'; }
start=$(date +%s)
timeout=${1:-600}

ready() {
	n=$(k get dragonfly dragonfly -o jsonpath='{.spec.replicas}')
	[ "$(k get dragonfly dragonfly -o jsonpath='{.status.phase}')" = Ready ] || return 1
	[ "$(k get pods -l app=dragonfly -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}' | grep -o True | wc -l)" -eq "$n" ] || return 1
	master=$(k get pods -l app=dragonfly,role=master -o jsonpath='{.items[*].metadata.name}')
	[ "$(echo $master | wc -w)" -eq 1 ] || return 1
	[ "$(k get pods -l app=dragonfly,role=replica -o name | wc -l)" -eq $((n - 1)) ] || return 1
	[ "$(cli $master info replication | grep -c 'state=online')" -eq $((n - 1)) ] || return 1
	for p in $(k get pods -l app=dragonfly,role=replica -o jsonpath='{.items[*].metadata.name}'); do
		cli $p info replication | grep -qx master_link_status:up || return 1
	done
	[ "$(k get endpointslices -l kubernetes.io/service-name=dragonfly -o jsonpath='{.items[*].endpoints[*].addresses[*]}')" = \
		"$(k get pod $master -o jsonpath='{.status.podIP}')" ]
}

until ready; do
	if [ $(($(date +%s) - start)) -ge "$timeout" ]; then
		echo "not ready after ${timeout}s"
		k get dragonfly,pods -L role
		exit 1
	fi
	sleep 3
done
echo "ready after $(($(date +%s) - start))s: master $master + $((n - 1)) replicas"
