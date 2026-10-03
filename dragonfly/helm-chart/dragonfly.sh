#!/bin/sh
# ./dragonfly.sh wait|test|failover|status
set -eu

k="kubectl --context kind-${CLUSTER:-dragonfly-helm} -n ${NAMESPACE:-dragonfly}"

cli() { pod=$1; shift; $k exec "$pod" -- redis-cli "$@"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
retry() {
	n=$1; shift
	until "$@" >/dev/null 2>&1; do
		n=$((n - 1)); [ $n -gt 0 ] || fail "timed out: $*"; sleep 1
	done
}

field() { cli "$1" info replication | tr -d '\r' | sed -n "s/^$2://p"; }
primary_ok() { [ "$(field dragonfly-0 role)" = master ] && [ "$(field dragonfly-0 connected_slaves)" = 2 ]; }
replica_ok() { [ "$(field "$1" master_link_status)" = up ] && [ "$(field "$1" master_host)" = dragonfly-0.dragonfly ]; }
link_down() { [ "$(field "$1" master_link_status)" = down ]; }
synced() { [ "$(cli "$1" dbsize)" = "$(cli dragonfly-0 dbsize)" ]; }

# write <prefix> <count>: SETs through Service dragonfly-primary
write() {
	out=$(seq "$2" | awk -v p="$1" '{ print "SET " p ":" $1 " v" $1 }' |
		$k exec -i dragonfly-1 -- redis-cli -h dragonfly-primary)
	[ "$(echo "$out" | grep -cx OK)" = "$2" ] || fail "not every SET $1:* returned OK"
	echo "wrote $1:1..$2"
}

# read_keys <pod> <prefix> <count>
read_keys() {
	got=$(seq "$3" | awk -v p="$2" '{ print "GET " p ":" $1 }' |
		$k exec -i "$1" -- redis-cli | awk '$0 == "v" NR { n++ } END { print n + 0 }')
	[ "$got" = "$3" ] || fail "$1: $got of $3 keys $2:* read back"
	echo "$1: read $2:1..$3"
}

readonly_replica() { cli "$1" set x x 2>&1 | grep -qi readonly || fail "$1 accepted a write"; }

replication_ok() {
	retry 60 primary_ok
	retry 60 replica_ok dragonfly-1
	retry 60 replica_ok dragonfly-2
}

case "${1:-}" in
wait)
	replication_ok
	;;
test)
	replication_ok
	echo "dragonfly-0 primary, dragonfly-1/2 replicas"
	write key 1000
	for r in dragonfly-1 dragonfly-2; do retry 30 synced $r; done
	for p in dragonfly-0 dragonfly-1 dragonfly-2; do read_keys $p key 1000; done
	readonly_replica dragonfly-1
	readonly_replica dragonfly-2
	echo "replicas reject writes"
	sum=$(cli dragonfly-0 -h dragonfly-primary eval "return redis.call('incrby', KEYS[1], ARGV[1])" 1 counter 5)
	[ "$sum" -ge 5 ] || fail "EVAL returned $sum"
	echo "EVAL incrby counter 5 -> $sum"
	cli dragonfly-0 info server | tr -d '\r' | grep -E '^(dragonfly_version|redis_mode|thread_count):'
	cli dragonfly-0 info memory | tr -d '\r' | grep -E '^(used_memory_human|maxmemory_human):'
	;;
failover)
	write key 1000
	write last 100
	for r in dragonfly-1 dragonfly-2; do retry 30 synced $r; done
	echo "deleting dragonfly-0"
	$k delete pod dragonfly-0
	retry 60 link_down dragonfly-1
	for r in dragonfly-1 dragonfly-2; do read_keys $r last 100; readonly_replica $r; done
	echo "no replica was promoted: dragonfly-1 is $(field dragonfly-1 role)"
	$k wait --for=create pod/dragonfly-0 --timeout=60s
	$k wait --for=condition=Ready pod/dragonfly-0 --timeout=3m
	$k logs dragonfly-0 | grep -E 'Loading /data/|Load finished' || fail "dragonfly-0 did not load a snapshot"
	read_keys dragonfly-0 key 1000
	read_keys dragonfly-0 last 100
	replication_ok
	echo "dragonfly-0 is back as primary with its data, replicas reconnected"
	write failover 100
	for r in dragonfly-1 dragonfly-2; do retry 30 synced $r; read_keys $r failover 100; done
	;;
status)
	$k get pods,svc,pvc -o wide
	for p in dragonfly-0 dragonfly-1 dragonfly-2; do
		echo "$p: $(cli $p info replication | tr -d '\r' | grep -E '^(role|connected_slaves|master_link_status):' | xargs) keys=$(cli $p dbsize)"
	done
	cli dragonfly-0 info persistence | tr -d '\r' | grep -E '^(last_success_save|last_saved_file|loading):' || true
	;;
*) echo "usage: $0 wait|test|failover|status" >&2; exit 2 ;;
esac
