#!/bin/sh
# Checks for the 3-primary cluster, run by the Makefile:
#   cluster.sh wait      # until 3 servers are Enabled/Available and every database is online on all 3
#   cluster.sh test      # SHOW SERVERS / SHOW DATABASE, graph via neo4j:// routing, CREATE DATABASE demo2
#   cluster.sh failover  # stop the leader of `neo4j`, check re-election and routed writes, start it again
set -eu
cd "$(dirname "$0")"

NODES="neo4j-1 neo4j-2 neo4j-3"

# first running server: the one we exec cypher-shell in (neo4j-1 may be the one that is stopped)
alive() {
	for n in $NODES; do
		[ "$(docker inspect -f '{{.State.Running}}' "$n" 2>/dev/null)" = true ] && { echo "$n"; return; }
	done
	echo "no neo4j server running" >&2
	exit 1
}

# q NODE ARGS...: cypher-shell inside NODE (plain output), extra args are passed on
q() {
	_q=$1
	shift
	docker exec -i "$_q" cypher-shell -u neo4j -p demo-password --format plain "$@"
}

# v NODE ARGS...: last output line of a query, quotes stripped (a single value or "a, b, c")
v() { q "$@" 2>/dev/null | tail -n 1 | tr -d '"'; }

check() { # check WHAT WANT GOT
	if [ "$2" = "$3" ]; then
		echo "ok: $1 = $3"
	else
		echo "FAIL: $1: want '$2', got '$3'" >&2
		exit 1
	fi
}

servers_ok() { # number of servers Enabled + Available, as seen by $1
	v "$1" -d system "SHOW SERVERS YIELD state, health WHERE state = 'Enabled' AND health = 'Available' RETURN count(*) AS n"
}

# "rows, online, databases" over all database allocations; healthy when online = rows = 3 * databases
dbs_state() {
	v "$1" -d system "SHOW DATABASES YIELD name, currentStatus
		RETURN count(*) AS rows, sum(CASE currentStatus WHEN 'online' THEN 1 ELSE 0 END) AS online,
		       count(DISTINCT name) AS dbs"
}

online_primaries() { # primaries of database $2 that are online, as seen by $1
	v "$1" -d system "SHOW DATABASE $2 YIELD role, currentStatus WHERE role = 'primary' AND currentStatus = 'online' RETURN count(*)"
}

writer() { # address (host:port) of the leader of database $2, as seen by $1
	v "$1" -d system "SHOW DATABASE $2 YIELD address, writer WHERE writer RETURN address"
}

wait_healthy() {
	echo "==> waiting for 3 healthy containers, 3 Enabled/Available servers and every database online on all 3 (max ${1}s)"
	i=0
	while :; do
		n=$(alive)
		h=$(for x in $NODES; do docker inspect -f '{{.State.Health.Status}}' "$x"; done | grep -c '^healthy$' || true)
		s=$(servers_ok "$n" || true)
		d=$(dbs_state "$n" || true)
		# d is "rows, online, dbs"
		set -- "$1" $(echo "$d" | tr -d ',')
		if [ "$h" = 3 ] && [ "$s" = 3 ] && [ $# = 4 ] && [ "$2" = "$3" ] && [ "$2" = $(($4 * 3)) ]; then
			echo "ok: 3 containers healthy, 3 servers available, $4 databases x 3 allocations online (after ${i}s)"
			return
		fi
		i=$((i + 2))
		[ "$i" -le "$1" ] || { echo "FAIL: cluster not healthy after ${1}s (healthy containers: '$h', servers: '$s', rows/online/dbs: '$d')" >&2; exit 1; }
		sleep 2
	done
}

cmd_test() {
	n=$(alive)
	echo "==> SHOW SERVERS"
	q "$n" -d system "SHOW SERVERS YIELD name, address, state, health, hosting ORDER BY address"
	check "servers Enabled + Available" 3 "$(servers_ok "$n")"

	echo "==> SHOW DATABASE neo4j (writer = Raft leader, the others are followers)"
	q "$n" -d system "SHOW DATABASE neo4j YIELD name, address, role, writer, currentStatus ORDER BY address"
	check "neo4j: online primaries" 3 "$(online_primaries "$n" neo4j)"
	check "neo4j: writers (leaders)" 1 "$(v "$n" -d system "SHOW DATABASE neo4j YIELD writer WHERE writer RETURN count(*)")"
	leader=$(writer "$n" neo4j)
	echo "leader of neo4j: $leader"

	echo "==> routing table for neo4j, as a neo4j:// driver gets it"
	q "$n" -d neo4j "CALL dbms.routing.getRoutingTable({}, 'neo4j') YIELD servers
		UNWIND servers AS s RETURN s.role AS role, s.addresses AS addresses ORDER BY role"
	check "routing WRITE address = leader" "$leader" "$(v "$n" -d neo4j "CALL dbms.routing.getRoutingTable({}, 'neo4j')
		YIELD servers UNWIND servers AS s WITH s WHERE s.role = 'WRITE' RETURN s.addresses[0]")"

	for f in cypher/01-graph.cypher cypher/02-read.cypher; do
		mode=write
		case $f in *read*) mode=read ;; esac
		echo "-- $f (neo4j://$n:7687, --access-mode $mode)"
		cat "$f"
		echo "--"
		q "$n" -a "neo4j://$n:7687" -d neo4j --access-mode "$mode" <"$f"
	done
	check "persons (routed read)" 3 "$(v "$n" -a "neo4j://$n:7687" --access-mode read -d neo4j "MATCH (p:Person) RETURN count(p)")"
	check "Alice -> Carol route (routed read)" "[Alice, Bob, Carol]" "$(v "$n" -a "neo4j://$n:7687" --access-mode read -d neo4j \
		"MATCH p = (:Person {name: 'Alice'})-[:KNOWS*]->(:Person {name: 'Carol'}) RETURN [x IN nodes(p) | x.name]")"

	echo "-- cypher/03-demo2.cypher"
	cat cypher/03-demo2.cypher
	echo "--"
	q "$n" -d system <cypher/03-demo2.cypher
	i=0
	until [ "$(online_primaries "$n" demo2)" = 3 ]; do
		i=$((i + 1))
		[ "$i" -le 60 ] || break
		sleep 1
	done
	q "$n" -d system "SHOW DATABASE demo2 YIELD name, address, role, writer, currentStatus ORDER BY address"
	check "demo2: online primaries" 3 "$(online_primaries "$n" demo2)"
	check "demo2: writers (leaders)" 1 "$(v "$n" -d system "SHOW DATABASE demo2 YIELD writer WHERE writer RETURN count(*)")"
	q "$n" -a "neo4j://$n:7687" -d demo2 "MERGE (h:Hello {db: 'demo2'}) RETURN h.db AS written"
}

cmd_failover() {
	n=$(alive)
	old=$(writer "$n" neo4j)
	node=${old%%:*}
	case " $NODES " in *" $node "*) ;; *) echo "FAIL: no leader found for neo4j (got '$old')" >&2; exit 1 ;; esac
	echo "==> leader of neo4j is $old; stopping $node"
	docker compose stop "$node"
	n=$(alive)

	echo "==> waiting for a new leader of neo4j (max 60s, asking $n)"
	i=0
	while :; do
		new=$(writer "$n" neo4j || true)
		[ -n "$new" ] && [ "$new" != "$old" ] && break
		i=$((i + 1))
		[ "$i" -le 60 ] || { echo "FAIL: no new leader after 60s (writer: '$new')" >&2; exit 1; }
		sleep 1
	done
	echo "ok: new leader $new (after ~${i}s)"
	q "$n" -d system "SHOW DATABASE neo4j YIELD address, role, writer, currentStatus ORDER BY address"
	check "servers Enabled + Available" 2 "$(servers_ok "$n")"

	echo "==> routed write + read with $node down (neo4j://$n:7687)"
	q "$n" -a "neo4j://$n:7687" -d neo4j "CREATE (f:Failover {stopped: '$node', at: toString(datetime())}) RETURN f.stopped, f.at"
	want=$(v "$n" -a "neo4j://$n:7687" -d neo4j "MATCH (f:Failover) RETURN count(f)")
	check "Failover node written (count > 0)" true "$([ "${want:-0}" -gt 0 ] && echo true || echo false)"
	q "$n" -a "neo4j://$n:7687" -d demo2 "MERGE (h:Hello {db: 'demo2'}) SET h.failover = '$node' RETURN h.db, h.failover"

	echo "==> starting $node"
	docker compose start "$node"
	wait_healthy 180
	q "$n" -d system "SHOW SERVERS YIELD name, address, state, health ORDER BY address"
	q "$n" -d system "SHOW DATABASES YIELD name, address, role, writer, currentStatus WHERE name <> 'system'
		RETURN name, address, role, writer, currentStatus ORDER BY name, address"

	echo "==> $node caught up: the write made while it was down, read directly on it (bolt://, read mode)"
	i=0
	while :; do
		got=$(v "$node" -a bolt://localhost:7687 --access-mode read -d neo4j "MATCH (f:Failover) RETURN count(f)" || true)
		[ "$got" = "$want" ] && break
		i=$((i + 1))
		[ "$i" -le 60 ] || break
		sleep 1
	done
	check "Failover nodes on $node" "$want" "$got"
}

case "${1:-}" in
wait) wait_healthy "${2:-180}" ;;
test) cmd_test ;;
failover) cmd_failover ;;
*) echo "usage: $0 wait [seconds] | test | failover" >&2; exit 2 ;;
esac
