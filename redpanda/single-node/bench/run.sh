#!/bin/bash
# `make benchmark` (bench service: the redpanda image, rpk only, separate container).
# rpk is a Kafka client (franz-go), not a load generator: this measures one rpk producer and one
# rpk consumer against the broker, which is what you get from shell pipelines.
#   produce: RECORDS records of RECORD_SIZE bytes from a file, acks=all, no compression
#   consume: the same records back from offset 0
set -euo pipefail
: "${NAME:?}" "${RECORDS:?}" "${RECORD_SIZE:?}" "${PARTITIONS:?}"
OUT=/results/$NAME.txt
X="-X brokers=${BROKERS:-redpanda:9092}"
log() { echo "$*" | tee -a "$OUT"; }

: >"$OUT"
log "meta: date=$(date -u +%FT%TZ) rpk=$(rpk --version | awk '{print $5}') records=$RECORDS record_size=$RECORD_SIZE partitions=$PARTITIONS"
log "meta: limits=${BENCH_LIMITS:-none}"
log "meta: host=${HOST_INFO:-?} docker=${DOCKER_INFO:-?}"
rpk topic delete bench $X >/dev/null 2>&1 || true
rpk topic create bench $X -p "$PARTITIONS" -r 1 >/dev/null

# input file: RECORDS lines of RECORD_SIZE bytes (sequence number + padding)
awk -v n="$RECORDS" -v s="$RECORD_SIZE" 'BEGIN { pad = sprintf("%*s", s, ""); gsub(/ /, "x", pad);
	for (i = 0; i < n; i++) { id = sprintf("%012d|", i); print id substr(pad, 1, s - length(id)) } }' >/tmp/in.txt

ms() { date +%s%3N; }
t0=$(ms)
rpk topic produce bench $X --acks -1 -z none -o '' </tmp/in.txt
t1=$(ms)
rpk topic consume bench $X --offset start --num "$RECORDS" --format '%v\n' | wc -l >/tmp/count
t2=$(ms)
got=$(cat /tmp/count)
mb=$(awk -v n="$RECORDS" -v s="$RECORD_SIZE" 'BEGIN { print n * s / 1048576 }')
p=$(awk -v n="$RECORDS" -v mb="$mb" -v t=$((t1 - t0)) 'BEGIN { printf "%.0f records/s, %.1f MiB/s, %.2f s", n / t * 1000, mb / t * 1000, t / 1000 }')
c=$(awk -v n="$got" -v mb="$mb" -v t=$((t2 - t1)) 'BEGIN { printf "%.0f records/s, %.1f MiB/s, %.2f s", n / t * 1000, mb / t * 1000, t / 1000 }')
log "produce: $RECORDS records ($mb MiB): $p"
log "consume: $got records: $c"
rpk topic describe bench $X --print-partitions | tee -a "$OUT"
rpk topic delete bench $X >/dev/null
[ "$got" = "$RECORDS" ] || { log "FAIL: consumed $got of $RECORDS"; exit 1; }
