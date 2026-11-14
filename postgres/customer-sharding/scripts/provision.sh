#!/bin/sh
set -eu
for shard in 0 1 2; do
 node=$(sh scripts/primary.sh "$shard")
 docker compose exec -T "$node" psql -U postgres -d postgres -v ON_ERROR_STOP=1 < sql/schema.sql
done
docker compose run --rm -T -e PGPASSWORD=local-admin client psql -h pgdog -p 6432 -U admin -d admin -v ON_ERROR_STOP=1 -c RELOAD
for attempt in $(seq 60); do
 if docker compose run --rm -T client pg_isready -h pgdog -p 6432 -U postgres -d orders; then exit 0; fi
 sleep 1
done
echo 'PgDog SQL endpoint did not become ready' >&2
exit 1
