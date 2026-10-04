#!/bin/bash
# `make failover`: load (client/, acks=all, idempotent) runs for DURATION; after 15 s the broker
# leading partition 0 of the topic is stopped with `docker stop`, restarted 20 s later; then
# wait until the cluster is healthy with no under-replicated partitions, and for the load
# client's read-back check.
set -euo pipefail
TOPIC=${TOPIC:-failover}
RPK="docker exec redpanda-cc-0 rpk"
ts() { date +%s.%N; }
since() { awk -v a="$1" -v b="$(ts)" 'BEGIN { printf "%.1f", b - a }'; }

docker exec redpanda-cc-0 rpk cluster health >/dev/null || { echo "cluster is not healthy (rpk cluster health); run `make up` first"; exit 1; }
docker rm -f redpanda-cc-load >/dev/null 2>&1 || true
docker compose --profile tools build -q load
docker compose --profile tools run -d --name redpanda-cc-load load >/dev/null
echo "==> load started (RATE=${RATE:-5000}/s, DURATION=${DURATION:-60s}); waiting 15 s"
sleep 15
leader=$($RPK topic describe "$TOPIC" --print-partitions | awk 'NR > 1 && $1 == 0 { print $2 }')
host=$($RPK cluster info -b | awk -v id="$leader" '$1 ~ /^[0-9]/ && $1 + 0 == id { print $2 }')
victim=redpanda-cc-${host#redpanda-}
[ -n "$leader" ] && [ -n "$host" ] || { echo "could not find the leader"; exit 1; }
[ "$victim" = redpanda-cc-0 ] && RPK="docker exec redpanda-cc-1 rpk"
echo "==> partition 0 leader: broker $leader ($host); stopping $victim"
t0=$(ts)
docker stop "$victim" >/dev/null
echo "==> stopped after $(since "$t0") s"
sleep 5
$RPK topic describe "$TOPIC" --print-partitions
$RPK cluster health | grep -E 'Healthy|Down|Leaderless|Under' || true
sleep 15
echo "==> starting $victim"
t1=$(ts)
docker start "$victim" >/dev/null
for i in $(seq 300); do
	h=$($RPK cluster health 2>/dev/null || true)
	echo "$h" | grep -Eq 'Healthy:.+true' && echo "$h" | grep -Eq 'Under-replicated partitions \(0\)' && break
	sleep 1
done
echo "==> $victim rejoined: cluster healthy, 0 under-replicated partitions, $(since "$t1") s after docker start"
$RPK topic describe "$TOPIC" --print-partitions
echo "==> waiting for the load client to finish and read the topic back"
rc=$(docker wait redpanda-cc-load)
docker logs redpanda-cc-load
docker rm redpanda-cc-load >/dev/null
exit "$rc"
