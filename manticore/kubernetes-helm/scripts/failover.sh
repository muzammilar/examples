#!/bin/sh
# make failover: load on worker-0..2 (pod DNS names); KILL_AFTER s in, worker-1 is
# force-deleted (no graceful shutdown); the StatefulSet recreates it on its PVC and it rejoins.
set -eu
. scripts/lib.sh
KILL_AFTER=${KILL_AFTER:-15}
mkdir -p results
start_load "$(nodes 3)" "$(nodes 3)"
ev "load started (${DURATION:-60}s)"
sleep "$KILL_AFTER"
ev "kubectl delete pod $W-1 --force --grace-period=0"
K delete pod $W-1 --force --grace-period=0 >/dev/null 2>&1
for i in $(seq 60); do [ "$(cs 0 size)" = 2 ] && break; sleep 0.5; done
ev "worker-0 sees size $(cs 0 size), status $(cs 0 status)"
K wait pod/$W-1 --for=condition=Ready --timeout=5m >/dev/null
ev "worker-1 Ready: $(cs 1 node_state), size $(cs 0 size)"
rc=0; wait_load || rc=$?
ev "load finished"
cat results/load.txt
exit $rc
