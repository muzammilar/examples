#!/bin/sh
set -eu
shard=${FAULT_SHARD:-1}
case "$shard" in 0|1|2|3) ;; *) echo 'FAULT_SHARD must be 0, 1, 2 or 3' >&2;exit 1;; esac
old=$(sh scripts/primary.sh "$shard")
restore() { docker compose up -d "$old"; }
trap restore EXIT INT TERM
sh scripts/phase.sh failure
started=$(date +%s)
printf 'Killing primary %s of shard %s while workload continues\n' "$old" "$shard"
docker compose kill -s SIGKILL "$old"
new=
for attempt in $(seq 90); do
 new=$(sh scripts/primary.sh "$shard" || true)
 if [ -n "$new" ] && [ "$new" != "$old" ]; then break; fi
 sleep 1
done
[ -n "$new" ] && [ "$new" != "$old" ] || { echo 'No new primary elected within 90 polls' >&2; exit 1; }
elapsed=$(( $(date +%s)-started ))
printf '{"shard":%s,"old_primary":"%s","new_primary":"%s","promotion_seconds":%s}\n' "$shard" "$old" "$new" "$elapsed" | tee ${RESULTS_DIR:-results/manual}/failover.json
restore
trap - EXIT INT TERM
docker compose up -d --wait --wait-timeout 180 "$old"
sh scripts/phase.sh recovery
