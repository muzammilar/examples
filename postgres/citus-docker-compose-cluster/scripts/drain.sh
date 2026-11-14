#!/bin/sh
set -eu
node=$(sh scripts/primary.sh 0)
sh scripts/phase.sh drain
started=$(date +%s)
for group in 4 5;do
 worker=$(sh scripts/primary.sh "$group")
 docker compose exec -T "$node" psql -U postgres -v ON_ERROR_STOP=1 -c "SELECT citus_drain_node('$worker',5432)"
 # A removed group can no longer service deferred shard cleanup records.
 sh scripts/cleanup-shards.sh "$group"
 # Disable metadata synchronization while the worker is still reachable.
 docker compose exec -T "$node" psql -U postgres -v ON_ERROR_STOP=1 -c "SELECT citus_disable_node('$worker',5432)"
 # Stop the empty worker group before removing its Citus metadata.
 docker compose --profile scale stop "s${group}n0" "s${group}n1" "s${group}n2"
 for replica in 0 1 2;do
  docker compose exec -T "$node" psql -U postgres -v ON_ERROR_STOP=1 -c "SELECT citus_remove_node(nodename,nodeport) FROM pg_dist_node WHERE nodename='s${group}n${replica}' AND nodeport=5432"
 done
done
count=$(docker compose exec -T "$node" psql -U postgres -Atqc "SELECT count(*) FROM pg_dist_node WHERE groupid>0 AND noderole='primary' AND isactive")
[ "$count" = 3 ] || { echo 'Expected three worker groups after draining' >&2;exit 1; }
printf '{"operation":"scale-in","worker_groups":3,"drain_seconds":%s}\n' "$(( $(date +%s)-started ))" | tee ${RESULTS_DIR:-results/manual}/failover.json
sh scripts/phase.sh recovery
