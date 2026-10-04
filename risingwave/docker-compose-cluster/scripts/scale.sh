#!/usr/bin/env bash
# scale.sh out|in: add compute-3 and compute-4 (2 -> 4 nodes) or unregister and stop them
# (4 -> 2), while a datagen source feeds an aggregate MV and loader/main.go writes and reads.
# Prints throughput of the MV before / during / after, how long rescheduling took, and the
# loader's failed requests.
set -euo pipefail
cd "$(dirname "$0")/.."
MODE=${1:?out|in}
SAMPLE=${SAMPLE:-20}   # seconds per throughput sample
mkdir -p results
OUT=results/scale-$MODE-$(date -u +%Y%m%dT%H%M%SZ).txt

psql() { docker compose --profile tools run --rm -T psql -v ON_ERROR_STOP=1 -X "$@" 2>/dev/null; }
ts() { date -u +%H:%M:%S; }
log() { echo "$(ts) $*" | tee -a "$OUT"; }
rows() { psql -tA -c "SELECT coalesce(sum(n), 0) FROM gen_by_key"; }
placement() { psql -tA -c "SELECT string_agg(w.host || '=' || c, ' ' ORDER BY w.host) FROM (SELECT worker_id, count(*) c FROM rw_actors GROUP BY worker_id) a JOIN rw_worker_nodes w ON w.id = a.worker_id"; }
rate() { # rows/s of the MV over $1 seconds
  local a b t0 t1
  a=$(rows); t0=$(date +%s.%N); sleep "$1"; b=$(rows); t1=$(date +%s.%N)
  awk -v a="$a" -v b="$b" -v t0="$t0" -v t1="$t1" 'BEGIN { printf "%.0f", (b - a) / (t1 - t0) }'
}

psql -q -f /sql/scale-setup.sql
psql -q -f /sql/failover-setup.sql
docker compose --profile tools build -q loader
LOAD_SECS=$((SAMPLE * 3 + 120))
docker compose --profile tools run --rm -T -e DURATION="$LOAD_SECS" loader > results/scale-loader.out 2>&1 &
LOADER=$!
sleep 5

log "placement before: $(placement)"
log "throughput before: $(rate "$SAMPLE") rows/s"

start=$(date +%s); r0=$(rows); t0=$(date +%s.%N)
if [ "$MODE" = out ]; then
  log "docker compose --profile scale up --detach --wait compute-3 compute-4"
  docker compose --profile scale up --detach --wait compute-3 compute-4 2>/dev/null
  want=4
else
  log "risingwave ctl meta unregister-workers --workers compute-3:5688,compute-4:5688"
  docker exec -e RW_META_ADDR=http://meta:5690 risingwave-cluster-meta \
    /risingwave/bin/risingwave ctl meta unregister-workers --workers compute-3:5688,compute-4:5688 --yes 2>&1 | tail -3 | tee -a "$OUT"
  log "docker stop compute-3 compute-4"
  docker stop risingwave-cluster-compute-3 risingwave-cluster-compute-4 >/dev/null
  want=2
fi
# rescheduled = actors on exactly $want compute nodes (adaptive parallelism)
until [ "$(placement | wc -w | tr -d ' ')" = "$want" ]; do
  (( $(date +%s) - start < 600 )) || { log "not rescheduled after 600s: $(placement)"; break; }
  sleep 2
done
r1=$(rows); t1=$(date +%s.%N)
log "rescheduled after $(( $(date +%s) - start ))s: $(placement)"
log "throughput during: $(awk -v a="$r0" -v b="$r1" -v t0="$t0" -v t1="$t1" 'BEGIN { printf "%.0f", (b - a) / (t1 - t0) }') rows/s"
sleep 5
log "throughput after: $(rate "$SAMPLE") rows/s"
psql -c "SELECT name, relation_type, parallelism FROM rw_streaming_parallelism ORDER BY name" | tee -a "$OUT"

wait "$LOADER" || true
sed -n '/^summary/,$p' results/scale-loader.out | tee -a "$OUT"
psql -q -c "FLUSH"
echo "written to $OUT"
