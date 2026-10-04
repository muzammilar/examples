#!/bin/bash
# rpk produces RECORDS records (acks=all) from redpanda-0 at ~RATE/s and prints each acked value;
# 10 s in, redpanda-2's pod is force-deleted. Afterwards every acked value must be consumable.
set -euo pipefail
K="kubectl --context kind-redpanda-operator -n redpanda"
RATE=${RATE:-500}; SECS=${SECS:-60}
mkdir -p results
EX="$K exec -i redpanda-0 -c redpanda --"
ts() { date +%s; }
$EX rpk topic delete failover >/dev/null 2>&1 || true
$EX rpk topic create failover --partitions 6 --replicas 3
echo "==> producing $((RATE * SECS)) records over ~${SECS} s"
# 1 line per record; 10 records every 1/RATE*10 s
$EX bash -c "for i in \$(seq 1 $((RATE * SECS))); do echo rec-\$i; [ \$((i % 10)) = 0 ] && sleep $(awk -v r=$RATE 'BEGIN { print 10 / r }'); done |
	rpk topic produce failover --delivery-timeout 60s -o '%v\n'" >results/acked.txt 2>results/produce.err &
pid=$!
sleep 10
echo "==> $(date -u +%T) force-deleting pod redpanda-2"
t0=$(ts)
$K delete pod redpanda-2 --force --grace-period=0 2>&1 | grep -v Warning || true
$K wait pod/redpanda-2 --for=condition=Ready --timeout=10m
for i in $(seq 120); do $EX rpk cluster health 2>/dev/null | grep -Eq 'Healthy:.+true' && break; sleep 2; done
echo "==> redpanda-2 back and cluster healthy $(( $(ts) - t0 )) s after the delete"
wait $pid || echo "producer exited with $? ($(head -3 results/produce.err))"
acked=$(sort -u results/acked.txt | grep -c '^rec-' || true)
$EX rpk topic consume failover --offset :end --format '%v\n' | sort -u >results/read.txt
lost=$(sort -u results/acked.txt | grep '^rec-' | comm -23 - results/read.txt | wc -l | tr -d ' ')
echo "acked $acked of $((RATE * SECS)), read back $(wc -l <results/read.txt | tr -d ' '), acked but missing: $lost"
$EX rpk cluster health
[ "$lost" = 0 ] || { echo FAIL; exit 1; }
echo OK
