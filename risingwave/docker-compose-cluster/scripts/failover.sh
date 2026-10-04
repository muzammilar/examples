#!/usr/bin/env bash
# Kill a compute node (SIGKILL) under write + read load, keep it down for DOWN seconds, start it
# again, then check every acknowledged row is in the table and in the MV.
set -euo pipefail
cd "$(dirname "$0")/.."

NODE=${NODE:-risingwave-cluster-compute-2}
KILL_AT=${KILL_AT:-15}     # seconds after the load starts
DOWN=${DOWN:-30}           # seconds the node stays down
DURATION=${DURATION:-$((KILL_AT + DOWN + 45))}
mkdir -p results
OUT=results/failover-$(date -u +%Y%m%dT%H%M%SZ).txt

psql() { docker compose --profile tools run --rm -T psql -v ON_ERROR_STOP=1 -X "$@" 2>/dev/null; }
ts() { date -u +%H:%M:%S; }

psql -q -f /sql/failover-setup.sql
docker compose --profile tools build -q loader

echo "$(ts) load starts: DURATION=${DURATION}s, kill $NODE at +${KILL_AT}s, down ${DOWN}s" | tee "$OUT"
docker compose --profile tools run --rm -T -e DURATION="$DURATION" loader > results/loader.out 2>&1 &
LOADER=$!

sleep "$KILL_AT"
echo "$(ts) docker kill $NODE" | tee -a "$OUT"
docker kill "$NODE" >/dev/null
for i in $(seq 1 "$DOWN"); do
  if (( i % 5 == 0 )); then
    echo "$(ts) +${i}s down: $(psql -tA -c "SELECT string_agg(host || '=' || state, ' ' ORDER BY id) FROM rw_worker_nodes WHERE type = 'WORKER_TYPE_COMPUTE_NODE'" || echo 'query failed')" | tee -a "$OUT"
  fi
  sleep 1
done
echo "$(ts) docker start $NODE" | tee -a "$OUT"
docker start "$NODE" >/dev/null
start=$(date +%s)
until docker inspect -f '{{.State.Health.Status}}' "$NODE" | grep -qx healthy; do sleep 1; done
echo "$(ts) $NODE healthy after $(( $(date +%s) - start ))s" | tee -a "$OUT"

wait "$LOADER" || true
cat results/loader.out | tee -a "$OUT"

echo "--- recovery events (rw_event_logs)" | tee -a "$OUT"
psql -c "SELECT timestamp, event_type FROM rw_event_logs WHERE timestamp > now() - interval '10 minutes' AND event_type ILIKE '%RECOVERY%' ORDER BY timestamp" | tee -a "$OUT"

echo "--- placement after recovery" | tee -a "$OUT"
psql -f /sql/02-placement.sql | tee -a "$OUT"

echo "--- verification" | tee -a "$OUT"
start=$(date +%s)
until psql -q -c "FLUSH" >/dev/null; do
  (( $(date +%s) - start < 300 )) || { echo "FLUSH still failing after 300s" | tee -a "$OUT"; exit 1; }
  sleep 2
done
echo "FLUSH succeeded $(( $(date +%s) - start ))s after the load ended" | tee -a "$OUT"
read -r ACK_NF ACK_F < <(sed -n 's/^ACKED no_flush=\([0-9]*\) implicit_flush=\([0-9]*\)$/\1 \2/p' results/loader.out)
rc=0
for t in no_flush:$ACK_NF flush:$ACK_F; do
  name=${t%%:*}; acked=${t#*:}
  read -r rows mv_n missing < <(psql -tA -F ' ' -c "SELECT (SELECT count(*) FROM events_$name), (SELECT n FROM events_${name}_total), $acked - (SELECT count(*) FROM events_$name WHERE id < $acked)")
  echo "events_$name: acked=$acked rows=$rows mv_n=$mv_n acked_missing=$missing" | tee -a "$OUT"
  [ "$rows" = "$mv_n" ] || { echo "  MV count differs from table" | tee -a "$OUT"; rc=1; }
  [ "$missing" = 0 ] || { echo "  acknowledged rows lost" | tee -a "$OUT"; [ "$name" = flush ] && rc=1; }
done
echo "written to $OUT"
exit $rc
