#!/bin/sh
set -eu
coordinator=$(sh scripts/primary.sh 0)
groups=$(docker compose exec -T "$coordinator" psql -U postgres -Atqc "SELECT DISTINCT groupid FROM pg_dist_node WHERE isactive AND noderole='primary' ORDER BY groupid")
for group in $groups; do
 primary=$(sh scripts/primary.sh "$group")
 lsn=$(docker compose exec -T "$primary" psql -U postgres -Atqc 'SELECT pg_current_wal_lsn()')
 for replica in 0 1 2; do
  node=s${group}n${replica}
  [ "$node" != "$primary" ] || continue
  caught_up=false
  for attempt in $(seq 60); do
   caught_up=$(docker compose exec -T "$node" psql -U postgres -Atqc "SELECT coalesce(pg_last_wal_replay_lsn() >= '$lsn'::pg_lsn,false)" 2>/dev/null || true)
   [ "$caught_up" = t ] && break
   sleep 1
  done
  [ "$caught_up" = t ] || { echo "$node did not replay to $lsn" >&2;exit 1; }
  printf '%s group=%s replayed_through=%s\n' "$node" "$group" "$lsn"
 done
done
