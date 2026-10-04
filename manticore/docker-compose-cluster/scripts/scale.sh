#!/bin/sh
# make scale-out: 3 -> 5 nodes under load. Starts manticore-4 and -5, JOIN CLUSTER; the
# sharding master (Buddy) notices the new nodes and moves shards of `events` onto them; the
# replicated table `logs` is copied to them by state transfer (SST).
# make scale-in: 5 -> 3 nodes under load. Stops manticore-5, waits until the sharding master
# has restored rf=2 for the shards it held on the remaining nodes, then the same for
# manticore-4; then ALTER CLUSTER c UPDATE nodes. One node at a time: with rf=2, two nodes
# leaving at once can take both copies of a shard.
# The load client connects to manticore-1..3 only (the nodes that exist the whole time).
set -eu
. scripts/lib.sh
STEADY=${STEADY:-30}
# paced writes (docs/s per table) keep 5 copies of `logs` small on a shared Docker VM disk
export RATE=${RATE:-1000}
mkdir -p results

wait_rebalanced() { # $1 = IP that must hold no shard ("" for none): wait until every shard copy is
	# active with rf ok, no copy is on $1, and the layout has not changed for 20 s (max 300 s)
	gone=$1; t=$(now); last=""; since=$(now)
	while :; do
		s=$(q 1 "SHOW SHARDING STATUS events" | cut -f2,3,4,8 | sort)
		bad=$(echo "$s" | awk -v g="$gone" '$3 != "active" || $4 != "ok" || (g != "" && $2 == g)' | wc -l)
		[ "$s" != "$last" ] && { last=$s; since=$(now); }
		stable=$(echo "$(now) $since" | awk '{ print ($1 - $2 >= 20) }')
		[ "$bad" -eq 0 ] && [ "$stable" = 1 ] && break
		if [ "$(echo "$(now) $t" | awk '{ print ($1 - $2 > 300) }')" = 1 ]; then ev "not settled after 300 s"; break; fi
		sleep 1
	done
	ev "layout settled: last change $(echo "$since $t" | awk '{ printf "%.1f", $1 - $2 }') s after the step: $(shard_map 1 events)"
}

case $1 in
out)
	export DURATION=${DURATION:-$((STEADY * 2 + 120))}
	export VERIFY_NODES=manticore-1:9306,manticore-2:9306,manticore-3:9306,manticore-4:9306,manticore-5:9306
	docker compose --profile load build -q load
	start_load
	ev "load started; 3 nodes: $(shard_map 1 events)"
	sleep "$STEADY"
	ev "docker compose up manticore-4 manticore-5"
	docker compose --profile scale up --detach --wait manticore-4 manticore-5 2>/dev/null
	for n in 4 5; do
		ev "manticore-$n: JOIN CLUSTER c AT 'manticore-1:9312'"
		q $n "JOIN CLUSTER c AT 'manticore-1:9312'"
		until [ "$(node_state $n)" = synced ]; do sleep 0.5; done
		ev "manticore-$n synced; cluster_c_size=$(q 1 "SHOW STATUS LIKE 'cluster_c_size'" | cut -f2)"
	done
	wait_rebalanced ""
	sleep "$STEADY"
	ev "stopping the load"
	docker stop -t 300 manticore-cc-load >/dev/null 2>&1 || true # SIGTERM: the client stops writing and verifies
	;;
in)
	export DURATION=${DURATION:-$((STEADY * 2 + 120))}
	docker compose --profile load build -q load
	start_load
	ev "load started; 5 nodes: $(shard_map 1 events)"
	sleep "$STEADY"
	for n in 5 4; do
		ev "docker compose stop manticore-$n"
		docker compose --profile scale stop manticore-$n 2>/dev/null
		ev "cluster_c_size=$(q 1 "SHOW STATUS LIKE 'cluster_c_size'" | cut -f2)"
		wait_rebalanced 172.28.30.1$n:9312
	done
	q 1 "ALTER CLUSTER c UPDATE nodes"
	ev "ALTER CLUSTER c UPDATE nodes: $(q 1 "SHOW STATUS LIKE 'cluster_c_nodes_set'" | cut -f2)"
	docker compose --profile scale rm -f manticore-4 manticore-5 >/dev/null 2>&1
	docker volume rm -f manticore-cluster_manticore-4 manticore-cluster_manticore-5 >/dev/null
	ev "manticore-4 and -5 removed (containers and volumes)"
	sleep "$STEADY"
	ev "stopping the load"
	docker stop -t 300 manticore-cc-load >/dev/null 2>&1 || true # SIGTERM: the client stops writing and verifies
	;;
*) echo "usage: $0 out|in" >&2; exit 2 ;;
esac
rc=0; wait_load || rc=$?
cat results/load.txt
exit $rc
