#!/bin/bash
# RF=3 topic: one Raft group per partition, leaders spread over the brokers; records written via
# one broker are read via another; schemas registered on one broker are served by all.
set -euo pipefail
set -x
rpk cluster info
rpk cluster info -b
rpk topic delete orders >/dev/null 2>&1 || true
rpk topic create orders --partitions 6 --replicas 3
rpk topic describe orders --print-partitions
seq 1 1000 | sed 's/^/order-/' | rpk topic produce orders -X brokers=redpanda-0:9092 -o ''
n=$(rpk topic consume orders -X brokers=redpanda-2:9092 --offset start --num 1000 --format '%v\n' | sort -u | wc -l)
leaders=$(rpk topic describe orders --print-partitions | awk 'NR > 1 { print $2 }' | sort -u | wc -l)
replicas=$(rpk topic describe orders --print-partitions | grep -o '\[[0-9 ]*\]' | sort -u)
# Schema Registry: written to the _schemas topic (RF 3), so every broker serves it
curl -sf -X POST http://redpanda-0:8081/subjects/orders-value/versions \
	-H 'Content-Type: application/vnd.schemaregistry.v1+json' \
	-d '{"schema":"{\"type\":\"record\",\"name\":\"Order\",\"fields\":[{\"name\":\"id\",\"type\":\"string\"}]}"}'
echo
sleep 1
sr=$(curl -sf http://redpanda-2:8081/subjects)
set +x
echo "consumed $n distinct records via redpanda-2; partition leaders on $leaders brokers; replica sets: $(echo $replicas)"
echo "subjects served by redpanda-2: $sr"
[ "$n" = 1000 ] || { echo "FAIL: expected 1000 records, got $n"; exit 1; }
[ "$leaders" -ge 2 ] || { echo "FAIL: all leaders on one broker"; exit 1; }
echo "$sr" | grep -q orders-value || { echo "FAIL: schema not visible on redpanda-2"; exit 1; }
echo "OK"
