#!/bin/sh
# make test: cluster state, then writes on one node and reads on another, for the replicated
# table `logs` and the sharded table `events`. Removes its rows at the end.
set -eu
. scripts/lib.sh
echo "==== cluster status on every node"
for n in 1 2 3; do
	echo "manticore-$n: $(q $n "SHOW STATUS LIKE 'cluster_c_status'" | cut -f2) / $(node_state $n), size $(q $n "SHOW STATUS LIKE 'cluster_c_size'" | cut -f2), tables: $(q $n "SHOW STATUS LIKE 'cluster_c_indexes'" | cut -f2)"
done
qt 1 "SHOW STATUS LIKE 'cluster_c_nodes_set'"

echo; echo "==== replicated table logs: write on manticore-2, read on manticore-3"
echo "-- writes to a replicated table need the cluster prefix; without it:"
q 2 "INSERT INTO logs (id, message) VALUES (1, 'x')" 2>&1 | sed 's/^/   /' || true
q 2 "INSERT INTO c:logs (id, message, service, level, status, latency_ms, ts) VALUES
 (1, 'payment gateway timeout after 30s', 'billing', 'error', 504, 30000, 1791100000),
 (2, 'user login ok', 'auth', 'info', 200, 12, 1791100001),
 (3, 'search index refreshed', 'search', 'info', 200, 340, 1791100002)"
qt 3 "SELECT id, service, status, HIGHLIGHT() FROM logs WHERE MATCH('timeout | login') ORDER BY id ASC"

echo; echo "==== sharded table events: 6 shards x rf 2 over 3 nodes; written and read by its plain name on any node"
qt 1 "SHOW SHARDING STATUS events"
qt 1 "SHOW SHARDING MASTER"
q 3 "INSERT INTO events (id, message, service, level, status, latency_ms, ts) VALUES
 (1, 'order created', 'cart', 'info', 201, 25, 1791100000), (2, 'order paid', 'billing', 'info', 200, 80, 1791100001),
 (3, 'card declined', 'billing', 'warn', 402, 95, 1791100002), (4, 'order shipped', 'worker', 'info', 200, 12, 1791100003),
 (5, 'refund failed: gateway error', 'billing', 'error', 502, 3000, 1791100004), (6, 'order delivered', 'worker', 'info', 200, 9, 1791100005)"
qt 1 "SELECT id, service, status, message FROM events ORDER BY id ASC"
qt 2 "SELECT service, COUNT(*) AS n, MAX(latency_ms) AS max_ms FROM events WHERE MATCH('order | gateway') GROUP BY service ORDER BY n DESC, service ASC"
echo "-- manticore-2 holds its shards as tables system.events_s<N>:"
q 2 "SHOW TABLES FROM system" | grep events_ | cut -f1 | tr "\n" " "; echo
echo "-- the public table is type='shard': one agent line per shard, listing the 2 nodes holding a"
echo "-- copy (mirrors; a query goes to one of them, retried on the other)"
q 2 "SHOW CREATE TABLE events OPTION force=1" | cut -f2 | sed 's/\\n/ /g' | grep -o -E "(agent|local)='[^']*'"

echo; echo "==== cleanup"
q 1 "DELETE FROM c:logs WHERE id IN (1,2,3)"
q 1 "DELETE FROM events WHERE id IN (1,2,3,4,5,6)"
for n in 1 2 3; do echo "manticore-$n: logs $(q $n "SELECT COUNT(*) FROM logs" ), events $(q $n "SELECT COUNT(*) FROM events")"; done
