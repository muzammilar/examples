#!/bin/sh
# Wait until the live cluster matches the ValkeyCluster spec. status.state alone
# is not enough: it stays Ready while every pod is being recreated.
set -e
k="kubectl --context kind-valkey-operator -n valkey"
cli="$k exec valkey-valkey-0-0-0 -c server -- env -u VALKEYCLI_AUTH valkey-cli"
start=$(date +%s)
timeout=${1:-600}

healthy() {
	set -- $($k get valkeycluster valkey -o jsonpath='{.spec.shards} {.spec.replicas} {.status.state}')
	[ "$3" = Ready ] || return 1
	ready=$($k get pods -l valkey.io/cluster=valkey \
		-o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}' | wc -w)
	[ "$ready" -eq $(($1 * ($2 + 1))) ] || return 1
	info=$($cli cluster info 2>/dev/null) || return 1
	echo "$info" | grep -q cluster_state:ok || return 1
	echo "$info" | grep -q cluster_slots_ok:16384 || return 1
	nodes=$($cli cluster nodes) || return 1
	[ "$(echo "$nodes" | wc -l)" -eq $(($1 * ($2 + 1))) ] || return 1
	[ "$(echo "$nodes" | awk '$3 ~ /master/ && $3 !~ /fail/ && $8 == "connected" && NF > 8' | wc -l)" -eq "$1" ] || return 1
	[ "$(echo "$nodes" | awk '$3 ~ /slave/ && $3 !~ /fail/ && $8 == "connected"' | wc -l)" -eq $(($1 * $2)) ]
}

until healthy; do
	if [ $(($(date +%s) - start)) -ge "$timeout" ]; then
		echo "not healthy after ${timeout}s"
		$k get valkeycluster,valkeynodes,pods
		exit 1
	fi
	sleep 5
done
echo "healthy after $(($(date +%s) - start))s"
