#!/bin/sh
set -eu
node=$(sh scripts/primary.sh 0)
ready=false
for attempt in $(seq 90);do
 count=$(docker compose exec -T "$node" psql -U postgres -Atqc "SELECT count(*) FROM pg_dist_node WHERE groupid>0 AND noderole='primary' AND isactive")
 [ "$count" = 5 ] && { ready=true;break; }
 sleep 1
done
[ "$ready" = true ] || { echo 'Five worker groups did not register' >&2;exit 1; }
sh scripts/phase.sh rebalance
started=$(date +%s)
docker compose exec -T "$node" psql -U postgres -v ON_ERROR_STOP=1 -c 'SELECT citus_rebalance_start()' -c 'SELECT citus_rebalance_wait()'
printf '{"operation":"scale-out","worker_groups":5,"rebalance_seconds":%s}\n' "$(( $(date +%s)-started ))" | tee ${RESULTS_DIR:-results/manual}/failover.json
sh scripts/phase.sh recovery
