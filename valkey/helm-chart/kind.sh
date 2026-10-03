#!/bin/sh
# ./kind.sh test|failover|status
set -eu

k="kubectl --context kind-${CLUSTER:-valkey-helm} -n ${NAMESPACE:-valkey}"

cli() { pod=$1; shift; $k exec "$pod" -c valkey -- valkey-cli "$@"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
retry() {
	n=$1; shift
	until "$@" >/dev/null 2>&1; do
		n=$((n - 1)); [ $n -gt 0 ] || fail "timed out: $*"; sleep 1
	done
}

field() { cli "$1" info replication | tr -d '\r' | sed -n "s/^$2://p"; }
primary_ok() { [ "$(field valkey-0 role)" = master ] && [ "$(field valkey-0 connected_slaves)" = 2 ]; }
replica_ok() { [ "$(field "$1" master_link_status)" = up ] && field "$1" master_host | grep -q '^valkey-0\.'; }
link_down() { [ "$(field "$1" master_link_status)" = down ]; }
endpoints() {
	$k get endpointslice -l kubernetes.io/service-name="$1" \
		-o jsonpath='{.items[*].endpoints[?(@.conditions.ready==true)].targetRef.name}' | tr ' ' '\n' | sort | xargs
}

# write <from-pod> <prefix> <count>: SETs through Service valkey, then WAIT 2 on the same connection
write() {
	out=$(seq "$3" | awk -v p="$2" '{ print "SET " p ":" $1 " v" $1 } END { print "WAIT 2 5000" }' |
		$k exec -i "$1" -c valkey -- valkey-cli -h valkey)
	[ "$(echo "$out" | grep -cx OK)" = "$3" ] || fail "not every SET $2:* returned OK"
	[ "$(echo "$out" | tail -1)" = 2 ] || fail "WAIT 2 returned $(echo "$out" | tail -1)"
	echo "wrote $2:1..$3, both replicas acked"
}

# read <pod> <prefix> <count> [valkey-cli args]
read_keys() {
	pod=$1 p=$2 count=$3; shift 3
	got=$(seq "$count" | awk -v p="$p" '{ print "GET " p ":" $1 }' |
		$k exec -i "$pod" -c valkey -- valkey-cli "$@" | awk '$0 == "v" NR { n++ } END { print n + 0 }')
	[ "$got" = "$count" ] || fail "$pod${*:+ $*}: $got of $count keys $p:* read back"
	echo "$pod${*:+ $*}: read $p:1..$count"
}

readonly_replica() { cli "$1" set x x 2>&1 | grep -q READONLY || fail "$1 accepted a write"; }

case "${1:-}" in
test)
	primary_ok || fail "valkey-0 is not a primary with 2 replicas"
	replica_ok valkey-1 && replica_ok valkey-2 || fail "replicas are not following valkey-0"
	[ "$(endpoints valkey)" = valkey-0 ] || fail "Service valkey -> $(endpoints valkey)"
	[ "$(endpoints valkey-read)" = "valkey-0 valkey-1 valkey-2" ] || fail "Service valkey-read -> $(endpoints valkey-read)"
	echo "valkey-0 primary, valkey-1/2 replicas; valkey -> valkey-0, valkey-read -> all three"
	write valkey-1 key 1000
	for p in valkey-0 valkey-1 valkey-2; do read_keys $p key 1000; done
	read_keys valkey-2 key 1000 -h valkey-read
	readonly_replica valkey-1
	readonly_replica valkey-2
	echo "replicas reject writes"
	;;
failover)
	write valkey-1 key 1000
	echo "deleting valkey-0"
	$k delete pod valkey-0
	retry 60 link_down valkey-1
	for r in valkey-1 valkey-2; do read_keys $r key 1000; readonly_replica $r; done
	echo "write through Service valkey with no primary: $(cli valkey-1 -h valkey set x x 2>&1 | head -1)"
	$k wait --for=create pod/valkey-0 --timeout=60s
	$k wait --for=condition=Ready pod/valkey-0 --timeout=3m
	retry 60 primary_ok
	retry 60 replica_ok valkey-1
	retry 60 replica_ok valkey-2
	echo "valkey-0 is back as primary, replicas reconnected"
	read_keys valkey-0 key 1000
	write valkey-2 failover 100
	for r in valkey-1 valkey-2; do read_keys $r failover 100; done
	;;
status)
	$k get pods,svc -o wide
	for p in valkey-0 valkey-1 valkey-2; do
		echo "$p: $(cli $p info replication | tr -d '\r' | grep -E '^(role|connected_slaves|master_link_status|master_repl_offset):' | xargs)"
	done
	;;
*) echo "usage: $0 test|failover|status" >&2; exit 2 ;;
esac
