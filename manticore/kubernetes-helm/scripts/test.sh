#!/bin/sh
# make test: cluster state on every worker; a table created on worker-0 and added to the
# cluster, written through the worker Service, read on every worker and through the balancer.
set -eu
. scripts/lib.sh
svc() { # SQL against a Service, from inside worker-1 (it has the mysql client)
	host=$1; shift; K exec "$W-1" -c worker -- mysql -h"$host" -P9306 --table -e "$*"
}
echo "==== workers"
for p in 0 1 2; do echo "$W-$p: $(cs $p status) / $(cs $p node_state), size $(cs $p size)"; done
echo "nodes: $(cs 0 nodes_set)"

echo; echo "==== CREATE TABLE logs on worker-0, ALTER CLUSTER manticore_cluster ADD logs"
echo "-- (autoAddTablesInCluster only adds tables that exist when a worker starts)"
q 0 "CREATE TABLE IF NOT EXISTS logs (message text, service string, level string, status int, latency_ms int, ts timestamp) engine='columnar'"
cs 0 indexes | grep -qw logs || q 0 "ALTER CLUSTER manticore_cluster ADD logs"
echo "cluster tables: $(cs 0 indexes)"

echo; echo "==== INSERT through the worker Service (ClusterIP over all workers; writes need the cluster prefix)"
svc $W-svc "INSERT INTO manticore_cluster:logs (id, message, service, level, status, latency_ms, ts) VALUES
	(1, 'payment gateway timeout after 30s', 'billing', 'error', 504, 30000, 1791100000),
	(2, 'user login ok', 'auth', 'info', 200, 12, 1791100001),
	(3, 'search index refreshed', 'search', 'info', 200, 340, 1791100002)"

echo; echo "==== the rows on every worker"
for p in 0 1 2; do echo "$W-$p: $(q $p "SELECT COUNT(*) FROM logs") rows"; done

echo; echo "==== SELECT through the balancer (a distributed table with the workers as mirrors, table_ha_strategy nodeads)"
for i in $(seq 30); do svc manticore-manticoresearch-balancer-svc "SELECT 1 FROM logs LIMIT 1" >/dev/null 2>&1 && break; sleep 1; done
svc manticore-manticoresearch-balancer-svc "SELECT id, service, status, HIGHLIGHT() FROM logs WHERE MATCH('timeout | login') ORDER BY id ASC; SHOW TABLES"
q 0 "DELETE FROM manticore_cluster:logs WHERE id IN (1,2,3)"
