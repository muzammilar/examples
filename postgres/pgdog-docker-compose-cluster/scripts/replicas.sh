#!/bin/sh
set -eu
for shard in 0; do
 primary=$(sh scripts/primary.sh "$shard")
 lsn=$(docker compose exec -T "$primary" psql -U postgres -Atqc 'SELECT pg_current_wal_lsn()')
 expected=$(docker compose exec -T "$primary" psql -U postgres -Atqc 'SELECT count(*),sum(op_id),sum(amount),sum(customer_id) FROM orders')
 for replica in 0 1 2; do
  node=s${shard}n${replica}
  if [ "$node" != "$primary" ]; then
   caught_up=false
   for attempt in $(seq 60); do
    caught_up=$(docker compose exec -T "$node" psql -U postgres -Atqc "SELECT coalesce(pg_last_wal_replay_lsn() >= '$lsn'::pg_lsn,false)" 2>/dev/null || true)
    [ "$caught_up" = t ] && break
    sleep 1
   done
   [ "$caught_up" = t ] || { echo "$node did not replay to $lsn" >&2;exit 1; }
  fi
  observed=$(docker compose exec -T "$node" psql -U postgres -Atqc 'SELECT count(*),sum(op_id),sum(amount),sum(customer_id) FROM orders')
  [ "$observed" = "$expected" ] || { echo "$node differs from $primary" >&2; exit 1; }
  printf '%s shard=%s matches=%s through_lsn=%s\n' "$node" "$shard" "$observed" "$lsn"
 done
done
