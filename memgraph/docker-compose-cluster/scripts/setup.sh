#!/bin/sh
# Replication topology: memgraph-main is MAIN, memgraph-replica-1 a SYNC replica,
# memgraph-replica-2 an ASYNC replica. Safe to run again (skips what is already set).
set -eu
q() { echo "$2" | mgconsole --host "$1" --output-format=csv | tail -n +2 | tr -d '"'; }

for r in memgraph-replica-1 memgraph-replica-2; do
	if [ "$(q $r 'SHOW REPLICATION ROLE;')" != replica ]; then
		echo "$r: SET REPLICATION ROLE TO REPLICA WITH PORT 10000"
		q $r 'SET REPLICATION ROLE TO REPLICA WITH PORT 10000;'
	fi
done
registered=$(q memgraph-main 'SHOW REPLICAS;' | cut -d, -f1)
echo "$registered" | grep -qx replica1 || {
	echo "memgraph-main: REGISTER REPLICA replica1 SYNC TO 'memgraph-replica-1:10000'"
	q memgraph-main "REGISTER REPLICA replica1 SYNC TO 'memgraph-replica-1:10000';"
}
echo "$registered" | grep -qx replica2 || {
	echo "memgraph-main: REGISTER REPLICA replica2 ASYNC TO 'memgraph-replica-2:10000'"
	q memgraph-main "REGISTER REPLICA replica2 ASYNC TO 'memgraph-replica-2:10000';"
}
echo "SHOW REPLICAS;" | mgconsole --host memgraph-main --output-format=tabular
