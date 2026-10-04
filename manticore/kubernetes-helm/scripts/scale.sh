#!/bin/sh
# make scale-out / scale-in: kubectl scale the worker StatefulSet to $1 replicas with the load
# running on worker-0..2. New pods join the cluster themselves (chart's worker script, state
# transfer from a running worker); removed pods leave it on shutdown.
set -eu
. scripts/lib.sh
to=$1
STEADY=${STEADY:-20}
export RATE=${RATE:-1000} # paced writes (rows/s): keeps the data the new workers copy small
mkdir -p results
from=$(K get statefulset $W -o jsonpath='{.spec.replicas}')
export DURATION=${DURATION:-$((STEADY * 2 + 600))} # upper bound; stopped with SIGTERM after the step
start_load "$(nodes 3)" "$(nodes "$to")"
ev "load started; $from workers, cluster size $(cs 0 size)"
sleep "$STEADY"
ev "kubectl scale statefulset $W --replicas=$to"
K scale statefulset $W --replicas=$to >/dev/null
until [ "$(cs 0 size)" = "$to" ] && [ "$(K get statefulset $W -o jsonpath='{.status.readyReplicas}')" = "$to" ]; do sleep 1; done
ev "cluster size $(cs 0 size), $to workers ready; nodes: $(cs 0 nodes_set)"
for p in $(seq 0 $((to - 1))); do echo "  $W-$p: $(q $p "SELECT COUNT(*) FROM logs") rows, $(cs $p node_state)"; done
sleep "$STEADY"
ev "stopping the load"
K exec load -- kill -TERM 1 2>/dev/null || true # the client stops, then counts rows on every node
rc=0; wait_load || rc=$?
cat results/load.txt
exit $rc
