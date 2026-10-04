#!/bin/sh
# Creates replication cluster `c` on manticore-1, joins manticore-2 and -3 (or the nodes given
# as arguments), then the tables. Idempotent: skips what already exists.
set -eu
q() { n=$1; shift; docker exec "manticore-cc-$n" mysql -h127.0.0.1 -P9306 --skip-table -N -e "$*"; }
status() { q "$1" "SHOW STATUS LIKE 'cluster_c_node_state'" | cut -f2; }

if [ -z "$(status 1)" ]; then
	echo "==> manticore-1: CREATE CLUSTER c"
	q 1 "CREATE CLUSTER c"
fi
for n in ${NODES:-2 3}; do
	if [ -z "$(status "$n")" ]; then
		echo "==> manticore-$n: JOIN CLUSTER c AT 'manticore-1:9312'"
		q "$n" "JOIN CLUSTER c AT 'manticore-1:9312'"
	fi
done
for n in 1 ${NODES:-2 3}; do
	for i in $(seq 60); do [ "$(status "$n")" = synced ] && break; sleep 1; done
	echo "manticore-$n: $(status "$n")"
done

[ -n "${NODES:-}" ] && exit 0 # joining extra nodes (scale-out): tables follow through replication
if [ -z "$(q 1 "SHOW TABLES LIKE 'logs'")" ]; then
	echo "==> replicated table logs (a full copy on every node)"
	q 1 "CREATE TABLE logs (message text, service string, level string, status int, latency_ms int, ts timestamp) engine='columnar'"
	q 1 "ALTER CLUSTER c ADD logs"
fi
if [ -z "$(q 1 "SHOW TABLES LIKE 'events'")" ]; then
	echo "==> sharded table events: 6 shards, 2 copies of each (rf=2) spread over the nodes"
	q 1 "CREATE TABLE c:events (message text, service string, level string, status int, latency_ms int, ts timestamp) engine='columnar' shards='6' rf='2' timeout='60'"
fi
q 1 "SHOW TABLES"
