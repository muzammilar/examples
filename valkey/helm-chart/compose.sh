#!/bin/sh
# ./compose.sh test|failover|status
set -eu
cd "$(dirname "$0")"

via=valkey-1 # node the checks go through; failover moves it off the stopped one

on() { n=$1; shift; docker compose exec -T "$n" valkey-cli "$@" </dev/null; }
fail() { echo "FAIL: $*" >&2; exit 1; }
retry() {
	n=$1; shift
	until "$@" >/dev/null 2>&1; do
		n=$((n - 1)); [ $n -gt 0 ] || fail "timed out: $*"; sleep 1
	done
}

# CLUSTER NODES as: hostname role primary-id id slots
nodes() {
	on $via cluster nodes | awk '{ split($2, h, ","); sub(/myself,/, "", $3)
		s = ""; for (i = 9; i <= NF; i++) s = s " " $i; print h[2], $3, $4, $1 s }' | sort
}
topology_ok() {
	nodes | awk '$2 == "master" && NF > 4 { p++ } $2 == "slave" { r++ } /fail|noaddr|handshake/ { bad++ }
		END { exit !(p == 3 && r == 3 && !bad) }'
}
# every replica has as many keys as its primary
caught_up() {
	n=$(nodes)
	echo "$n" | awk '$2 == "slave" { print $1, $3 }' | while read -r r id; do
		p=$(echo "$n" | awk -v id="$id" '$4 == id { print $1 }')
		[ "$(on "$p" dbsize)" = "$(on "$r" dbsize)" ] || exit 1
	done
}

write() {
	[ "$(seq "$2" | awk -v p="$1" '{ print "SET " p ":" $1 " v" $1 }' |
		docker compose exec -T $via valkey-cli -c | grep -cx OK)" = "$2" ] || fail "not every SET $1:* returned OK"
	echo "wrote $1:1..$2"
}
read_keys() {
	got=$(seq "$2" | awk -v p="$1" '{ print "GET " p ":" $1 }' | docker compose exec -T $via valkey-cli -c |
		grep -v '^->' | awk '$0 == "v" NR { n++ } END { print n + 0 }')
	[ "$got" = "$2" ] || fail "$got of $2 keys $1:* read back"
	echo "read $1:1..$2 via $via"
}

case "${1:-}" in
test)
	for n in 1 2 3 4 5 6; do
		info=$(on valkey-$n cluster info | tr -d '\r')
		for want in cluster_state:ok cluster_slots_assigned:16384 cluster_slots_ok:16384 cluster_known_nodes:6 cluster_size:3; do
			echo "$info" | grep -qx $want || fail "valkey-$n: no $want"
		done
	done
	echo "all six: cluster_state:ok, 16384 slots, 6 nodes, 3 shards"
	nodes
	topology_ok || fail "expected 3 primaries with slots + 3 replicas"
	[ "$(nodes | awk '$2 == "slave" { print $3 }' | sort -u | wc -l)" -eq 3 ] || fail "replicas not spread over 3 primaries"
	[ "$(nodes | grep -c '^valkey-[1-6] ')" -eq 6 ] || fail "not every node announces its hostname"
	on $via cluster shards | grep -q valkey- || fail "CLUSTER SHARDS has no hostnames"
	write key 1000
	read_keys key 1000
	for p in $(nodes | awk '$2 == "master" { print $1 }'); do
		c=$(on "$p" dbsize)
		echo "$p holds $c keys"
		[ "$c" -gt 0 ] || fail "$p holds no keys"
	done
	retry 30 caught_up
	echo "replicas caught up"
	;;
failover)
	write key 1000
	victim=$(nodes | awk '$2 == "master" && $5 ~ /^0-/ { print $1 }')
	id=$(nodes | awk -v h="$victim" '$1 == h { print $4 }')
	heir=$(nodes | awk -v id="$id" '$2 == "slave" && $3 == id { print $1 }')
	via=$heir
	echo "stopping $victim (primary of slot 0), its replica is $heir"
	docker compose stop "$victim"
	retry 60 sh -c "docker compose exec -T $heir valkey-cli role | head -1 | grep -qx master"
	retry 60 sh -c "docker compose exec -T $heir valkey-cli cluster info | grep -q cluster_state:ok"
	nodes
	read_keys key 1000
	write failover 100
	read_keys failover 100
	echo "starting $victim"
	docker compose up --detach --wait "$victim"
	hid=$(nodes | awk -v h="$heir" '$1 == h { print $4 }')
	retry 60 sh -c "docker compose exec -T $victim valkey-cli info replication | grep -q master_link_status:up"
	nodes | awk -v h="$victim" -v id="$hid" '$1 == h && $2 == "slave" && $3 == id { f = 1 } END { exit !f }' ||
		fail "$victim is not a replica of $heir"
	retry 60 topology_ok
	retry 30 caught_up
	nodes
	via=$victim
	read_keys failover 100
	;;
status)
	on $via cluster info | grep -E '^cluster_(state|slots_assigned|slots_ok|known_nodes|size):'
	{ echo "HOST ROLE PRIMARY-ID NODE-ID SLOTS"; nodes; } | column -t
	;;
*) echo "usage: $0 test|failover|status" >&2; exit 2 ;;
esac
