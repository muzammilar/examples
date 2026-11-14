#!/bin/sh
set -eu
shard=${FAULT_SHARD:--55}
case "$shard" in -55|55-aa|aa-) ;; *) echo 'Invalid Vitess shard' >&2;exit 1;;esac
old=$(sh scripts/primary.sh "$shard")
restore() { docker compose up -d "$old"; }
trap restore EXIT INT TERM
sh scripts/phase.sh failure
started=$(date +%s)
docker compose kill -s SIGKILL "$old"
new=
for attempt in $(seq 120); do
 new=$(sh scripts/primary.sh "$shard" || true)
 [ -n "$new" ] && [ "$new" != "$old" ] && break
 sleep 1
done
[ -n "$new" ] && [ "$new" != "$old" ] || { echo 'VTOrc did not promote a different tablet' >&2;exit 1; }
printf '{"shard":"%s","old_primary":"%s","new_primary":"%s","promotion_seconds":%s}\n' "$shard" "$old" "$new" "$(( $(date +%s)-started ))" | tee ${RESULTS_DIR:-results/manual}/failover.json
restore
trap - EXIT INT TERM
docker compose up -d --wait --wait-timeout 180 "$old"
sh scripts/phase.sh recovery
