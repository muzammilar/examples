#!/bin/sh
# make failover: load on all three nodes; KILL_AFTER s in, manticore-2 is killed (SIGKILL);
# DOWN_FOR s later it is started again and rejoins (incremental state transfer from a donor).
# The load verifies at the end that every acknowledged row is on every node.
set -eu
. scripts/lib.sh
KILL_AFTER=${KILL_AFTER:-15}
DOWN_FOR=${DOWN_FOR:-20}
VICTIM=${VICTIM:-2}
mkdir -p results
export DURATION=${DURATION:-60}
docker compose --profile load build -q load
start_load
ev "load started (${DURATION}s); shards: $(shard_map 1 events)"
sleep "$KILL_AFTER"
ev "docker kill manticore-cc-$VICTIM"
docker kill manticore-cc-$VICTIM >/dev/null
for i in $(seq 30); do
	[ "$(q 1 "SHOW STATUS LIKE 'cluster_c_size'" | cut -f2)" = 2 ] && break; sleep 0.5
done
ev "manticore-1 sees cluster_c_size=$(q 1 "SHOW STATUS LIKE 'cluster_c_size'" | cut -f2), status $(q 1 "SHOW STATUS LIKE 'cluster_c_status'" | cut -f2)"
sleep 5
ev "sharding after the kill: $(shard_map 1 events)"
sleep $((DOWN_FOR - 5))
ev "docker start manticore-cc-$VICTIM"
docker start manticore-cc-$VICTIM >/dev/null
until [ "$(node_state "$VICTIM")" = synced ]; do sleep 0.5; done
ev "manticore-$VICTIM node_state synced, cluster_c_size=$(q 1 "SHOW STATUS LIKE 'cluster_c_size'" | cut -f2)"
docker logs manticore-cc-$VICTIM 2>&1 | grep -i -E 'IST|SST|state transfer|synced' | tail -n 5 || true
rc=0; wait_load || rc=$?
ev "load finished (exit $rc); sharding: $(shard_map 1 events)"
cat results/load.txt
exit $rc
