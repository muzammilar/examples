#!/bin/sh
set -eu
fault=${FAULT_KIND:-data}
case "$fault" in
 data) target=${FAULT_NODE:-data1};case "$target" in data[1-9]) ;; *) echo 'Invalid data node' >&2;exit 1;;esac ;;
 sql) target=sql0 ;;
 management) target=mgm0 ;;
 *) echo 'Invalid NDB fault' >&2;exit 1 ;;
esac
restore() { docker compose up -d "$target"; }
trap restore EXIT INT TERM
sh scripts/phase.sh failure
started=$(date +%s)
docker compose kill -s SIGKILL "$target"
sleep 10
restore
trap - EXIT INT TERM
docker compose up -d --wait --wait-timeout 300 "$target"
printf '{"fault":"%s","node":"%s","outage_and_rejoin_seconds":%s}\n' "$fault" "$target" "$(( $(date +%s)-started ))" | tee ${RESULTS_DIR:-results/manual}/failover.json
sh scripts/phase.sh recovery
