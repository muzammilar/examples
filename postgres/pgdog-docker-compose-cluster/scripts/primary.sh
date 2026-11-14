#!/bin/sh
set -eu
shard=${1:-0}
for replica in 0 1 2; do
 node=s${shard}n${replica}
 if docker compose exec -T "$node" curl -fsS http://localhost:8008/primary >/dev/null 2>&1; then
  printf '%s\n' "$node"
  exit 0
 fi
done
exit 1
