#!/bin/sh
set -eu
group=$1
case "$group" in 4|5) ;; *) echo 'Expected scale worker group 4 or 5' >&2; exit 1;; esac
coordinator=$(sh scripts/primary.sh 0)
for attempt in $(seq 60); do
 docker compose exec -T "$coordinator" psql -U postgres -v ON_ERROR_STOP=1 -c 'CALL citus_cleanup_orphaned_resources()'
 pending=$(docker compose exec -T "$coordinator" psql -U postgres -Atqc "SELECT count(*) FROM pg_dist_cleanup WHERE node_group_id=$group")
 [ "$pending" != 0 ] || exit 0
 sleep 1
done
echo "Worker group $group still has deferred cleanup records; refusing to remove it" >&2
exit 1
