#!/bin/sh
set -eu
directory="results/$RUN_ID"
mkdir -p "$directory"
chmod 777 "$directory"
test ! -e "$directory/ready.json" || { echo 'Use a fresh RUN_ID' >&2; exit 1; }
docker compose run --rm -T client heavy --fault > "$directory/workload.log" 2>&1 &
pid=$!
target=
restore() {
 if [ -n "$target" ]; then docker compose up -d "$target"; fi
 kill "$pid" 2>/dev/null || true
}
trap restore EXIT INT TERM
for attempt in $(seq 180); do
 [ ! -f "$directory/ready.json" ] || break
 kill -0 "$pid" 2>/dev/null || { cat "$directory/workload.log"; exit 1; }
 sleep 1
done
test -f "$directory/ready.json" || { echo 'No running task found' >&2; exit 1; }
case "${FAULT_KIND:-worker}" in
 worker)
  target=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["worker"])' "$directory/ready.json")
  case "$target" in worker[1-3]) ;; *) exit 1;; esac ;;
 minio) target=minio1 ;;
 *) echo 'Unknown fault kind' >&2; exit 1 ;;
esac
kill -0 "$pid" 2>/dev/null || { echo 'Query completed before fault' >&2; exit 1; }
started=$(date +%s)
docker compose kill -s SIGKILL "$target"
sleep 10
docker compose up -d --wait --wait-timeout 180 "$target"
printf '{"node":"%s","outage_and_rejoin_seconds":%s}\n' "$target" "$(( $(date +%s)-started ))" | tee "$directory/failover.json"
target=
wait "$pid" || { cat "$directory/workload.log"; exit 1; }
trap - EXIT INT TERM
cat "$directory/workload.log"
