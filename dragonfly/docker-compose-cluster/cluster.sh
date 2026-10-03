#!/bin/sh
# ./cluster.sh init|test|migrate|failover|status
# There is no control plane in OSS Dragonfly: this script reads the topology from a node,
# edits it with jq and pushes the result to every node with DFLYCLUSTER CONFIG.
set -eu
cd "$(dirname "$0")"

via=dragonfly-1 # node the checks go through; failover moves it off the stopped one

on() { n=$1; shift; docker compose exec -T "$n" redis-cli "$@" </dev/null | tr -d '\r'; }
client() { docker compose --progress quiet run --rm -T tools valkey-cli -h $via -c; }
fail() { echo "FAIL: $*" >&2; exit 1; }
retry() {
	n=$1; shift
	until "$@" >/dev/null 2>&1; do
		n=$((n - 1)); [ $n -gt 0 ] || fail "timed out: $*"; sleep 1
	done
}

# the config a node is running, rebuilt from its CLUSTER NODES
current() {
	on $via cluster nodes | jq -Rn '[inputs | select(length > 0) | split(" ") | {
		id: .[0], ip: (.[1] | split(":")[0]), port: (.[1] | split(":")[1] | split("@")[0] | tonumber),
		role: (.[2] | sub("myself,"; "")), master: .[3], slots: .[8:]}] as $n |
		[$n[] | select(.role == "master") | . as $m | {
			slot_ranges: [.slots[] | split("-") | {start: (.[0] | tonumber), end: (.[-1] | tonumber)}],
			master: {id, ip, port},
			replicas: [$n[] | select(.master == $m.id) | {id, ip, port}]}]'
}
node() { jq -nc --arg id "$(on "$1" cluster myid)" --arg ip "$1" '{id: $id, ip: $ip, port: 6379}'; }
push() {
	for n in $(echo "$1" | jq -r '.[] | .master.ip, .replicas[].ip'); do
		[ "$(on "$n" dflycluster config "$1")" = OK ] || fail "$n rejected the config"
	done
}
# shard (master hostname) that owns a slot
owner() {
	current | jq -r --argjson s "$1" '.[] | select(any(.slot_ranges[]; .start <= $s and $s <= .end)) | .master.ip'
}
replica_of() { current | jq -r --arg m "$1" '.[] | select(.master.ip == $m) | .replicas[].ip'; }

cluster_ok() {
	for n in $(current | jq -r '.[] | .master.ip, .replicas[].ip'); do
		on "$n" cluster info | grep -qx cluster_state:ok || return 1
	done
}
caught_up() {
	for m in $(current | jq -r '.[].master.ip'); do
		for r in $(replica_of "$m"); do
			on "$r" info replication | grep -q master_link_status:up || return 1
			[ "$(on "$m" dbsize)" = "$(on "$r" dbsize)" ] || return 1
		done
	done
}

write() {
	[ "$(seq "$2" | awk -v p="$1" '{ print "SET " p ":" $1 " v" $1 }' | client | grep -cx OK)" = "$2" ] ||
		fail "not every SET $1:* returned OK"
	echo "wrote $1:1..$2 via $via"
}
read_keys() {
	got=$(seq "$2" | awk -v p="$1" '{ print "GET " p ":" $1 }' | client |
		grep -v '^->' | awk '$0 == "v" NR { n++ } END { print n + 0 }')
	[ "$got" = "$2" ] || fail "$got of $2 keys $1:* read back"
	echo "read $1:1..$2 via $via"
}
show() {
	current | jq -r '.[] | (.slot_ranges | map("\(.start)-\(.end)") | join(",")) as $s |
		"\(.master.ip) master \($s) \(.master.id)", (.replicas[] | "\(.ip) replica - \(.id)")' |
		while read -r h role slots id; do echo "$h $role $(on "$h" dbsize) $slots $id"; done |
		{ echo "HOST ROLE KEYS SLOTS NODE-ID"; cat; } | column -t
}

case "${1:-}" in
init)
	if on $via cluster info | grep -qx cluster_state:ok; then echo "already configured"; exit; fi
	cfg=$(jq -nc --argjson m1 "$(node dragonfly-1)" --argjson m2 "$(node dragonfly-2)" --argjson m3 "$(node dragonfly-3)" \
		--argjson r1 "$(node dragonfly-4)" --argjson r2 "$(node dragonfly-5)" --argjson r3 "$(node dragonfly-6)" '[
		{slot_ranges: [{start: 0, end: 5460}], master: $m1, replicas: [$r1]},
		{slot_ranges: [{start: 5461, end: 10922}], master: $m2, replicas: [$r2]},
		{slot_ranges: [{start: 10923, end: 16383}], master: $m3, replicas: [$r3]}]')
	push "$cfg"
	on dragonfly-4 replicaof dragonfly-1 6379
	on dragonfly-5 replicaof dragonfly-2 6379
	on dragonfly-6 replicaof dragonfly-3 6379
	retry 60 cluster_ok
	retry 60 caught_up
	show
	;;
test)
	for n in 1 2 3 4 5 6; do
		info=$(on dragonfly-$n cluster info)
		for want in cluster_state:ok cluster_slots_assigned:16384 cluster_known_nodes:6 cluster_size:3; do
			echo "$info" | grep -qx $want || fail "dragonfly-$n: no $want"
		done
	done
	echo "all six: cluster_state:ok, 16384 slots, 6 nodes, 3 shards"
	[ "$(on $via cluster slots | grep -c '^dragonfly-')" -eq 6 ] || fail "CLUSTER SLOTS does not list 6 nodes"
	[ "$(on $via cluster shards | grep -cx replica)" -eq 3 ] || fail "CLUSTER SHARDS does not list 3 replicas"
	write key 1000
	read_keys key 1000
	# without -c the node answers MOVED for every key it does not own
	moved=$(seq 1000 | awk '{ print "GET key:" $1 }' | docker compose exec -T $via redis-cli | grep -c '^MOVED [0-9]* dragonfly-')
	mine=$(on $via dbsize)
	[ $((moved + mine)) -eq 1000 ] || fail "$via: $mine own keys + $moved MOVED != 1000"
	echo "$via: $mine keys served, $moved MOVED redirects"
	for m in $(current | jq -r '.[].master.ip'); do [ "$(on "$m" dbsize)" -gt 0 ] || fail "$m holds no keys"; done
	retry 30 caught_up
	show
	echo "every replica has its master's keys"
	;;
migrate)
	write key 1000
	src=$(owner 0)
	dst=$(owner 8192)
	cfg=$(current)
	range=$(echo "$cfg" | jq -c --arg s "$src" '.[] | select(.master.ip == $s) | .slot_ranges[0] | {start, end: (.start + 999)}')
	echo "moving slots $range from $src to $dst"
	push "$(echo "$cfg" | jq -c --arg s "$src" --arg d "$dst" --argjson r "$range" '
		(.[] | select(.master.ip == $d).master) as $t |
		map(if .master.ip == $s then .migrations = [{slot_ranges: [$r], node_id: $t.id, ip: $t.ip, port: $t.port}] else . end)')"
	src_id=$(on $src cluster myid)
	retry 60 sh -c "docker compose exec -T $src redis-cli dflycluster slot-migration-status | grep -qx FINISHED"
	retry 60 sh -c "docker compose exec -T $dst redis-cli dflycluster slot-migration-status $src_id | grep -qx FINISHED"
	on $src dflycluster slot-migration-status | paste -sd' ' -
	# the migration is done, the slots still belong to $src until the final config says otherwise
	final=$(echo "$cfg" | jq -c --arg s "$src" --arg d "$dst" --argjson r "$range" '
		map(if .master.ip == $s then .slot_ranges[0].start = $r.end + 1
			elif .master.ip == $d then .slot_ranges = (.slot_ranges + [$r] | sort_by(.start)) else . end)')
	push "$final"
	retry 30 cluster_ok
	[ "$(owner "$(echo "$range" | jq .start)")" = "$dst" ] || fail "slot $range not owned by $dst"
	[ "$(owner "$(echo "$range" | jq .end)")" = "$dst" ] || fail "slot $range not owned by $dst"
	read_keys key 1000
	retry 30 caught_up
	show
	;;
failover)
	write key 1000
	victim=$(owner 0)
	heir=$(replica_of "$victim")
	via=$heir
	echo "stopping $victim (master of slot 0), its replica is $heir"
	docker compose stop "$victim"
	on "$heir" replicaof no one
	cfg=$(current | jq -c --arg v "$victim" --argjson h "$(node "$heir")" \
		'map(if .master.ip == $v then .master = $h | .replicas = [] else . end)')
	push "$cfg"
	retry 30 cluster_ok
	show
	read_keys key 1000
	write failover 100
	echo "starting $victim, it comes back with a new node id and joins as $heir's replica"
	docker compose up --detach --wait "$victim"
	push "$(current | jq -c --arg h "$heir" --argjson v "$(node "$victim")" \
		'map(if .master.ip == $h then .replicas = [$v] else . end)')"
	on "$victim" replicaof "$heir" 6379
	retry 60 cluster_ok
	retry 60 caught_up
	show
	via=$victim
	read_keys failover 100
	;;
status)
	on $via cluster info | grep -E '^cluster_(state|slots_assigned|known_nodes|size):'
	show
	;;
*) echo "usage: $0 init|test|migrate|failover|status" >&2; exit 2 ;;
esac
