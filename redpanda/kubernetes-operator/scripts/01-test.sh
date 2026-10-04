#!/bin/bash
# runs in redpanda-0: brokers, an RF=3 topic, produce via one broker and consume via another
set -euo pipefail
set -x
rpk cluster info -b
rpk cluster health
rpk topic delete orders >/dev/null 2>&1 || true
rpk topic create orders --partitions 6 --replicas 3
seq 1 1000 | sed 's/^/order-/' | rpk topic produce orders -o ''
n=$(rpk topic consume orders -X brokers=redpanda-2.redpanda.redpanda.svc.cluster.local:9093 --offset start --num 1000 --format '%v\n' | sort -u | wc -l)
rpk topic describe orders --print-partitions
set +x
[ "$n" = 1000 ] || { echo "FAIL: consumed $n of 1000"; exit 1; }
echo "OK: 1000 records, RF 3"
