#!/bin/sh
set -eu
shard=${FAULT_SHARD:-0}
primary=$(sh scripts/primary.sh "$shard")
replica=$(docker compose exec -T "$primary" psql -U postgres -Atqc "SELECT application_name FROM pg_stat_replication WHERE sync_state='sync' LIMIT 1")
case "$replica" in s${shard}n[012]) ;; *) echo 'No synchronous replica found' >&2;exit 1;; esac
restore() { docker compose unpause "$replica"; }
trap restore EXIT INT TERM
sh scripts/phase.sh replica-paused
started=$(date +%s)
docker compose pause "$replica"
sleep 10
restore
trap - EXIT INT TERM
sh scripts/phase.sh recovery
printf '{"fault":"synchronous-replica-paused","node":"%s","pause_seconds":%s}\n' "$replica" "$(( $(date +%s)-started ))" | tee ${RESULTS_DIR:-results/manual}/failover.json
