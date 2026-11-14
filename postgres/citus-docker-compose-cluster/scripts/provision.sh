#!/bin/sh
set -eu
node=$(sh scripts/primary.sh 0)
for attempt in $(seq 90); do
 count=$(docker compose exec -T "$node" psql -U postgres -Atqc "SELECT count(*) FROM pg_dist_node WHERE groupid > 0 AND noderole='primary' AND isactive" 2>/dev/null || true)
 [ "$count" = 3 ] && break
 sleep 2
done
[ "$count" = 3 ] || { echo 'Patroni did not register three Citus worker groups' >&2;exit 1; }
# Workload owns these tables; reset their distributed metadata on a clean provision.
docker compose exec -T "$node" psql -U postgres -v ON_ERROR_STOP=1 -c 'DROP TABLE IF EXISTS orders, customers'
docker compose exec -T "$node" psql -U postgres -v ON_ERROR_STOP=1 < sql/schema.sql
