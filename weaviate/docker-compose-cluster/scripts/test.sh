#!/bin/sh
# Members, a 3-shard collection with 3 replicas, QUORUM insert, placement, reads at ONE/QUORUM/ALL.
set -eu
. scripts/lib.sh

echo "==> GET /v1/nodes: members as weaviate-1 sees them"
nodes $N1
expect "$(curl -sS $N1/v1/nodes | jq '[.nodes[] | select(.status == "HEALTHY")] | length')" 3 "healthy nodes"
echo

curl -sS -X DELETE "$N1/v1/schema/Landmark" >/dev/null   # start clean, so the test is repeatable
rest POST "$N1/v1/schema" requests/01-create-collection.json \
  '{class, shards: .shardingConfig.desiredCount, replicationFactor: .replicationConfig.factor}'
rest POST "$N1/v1/batch/objects?consistency_level=QUORUM" requests/02-batch-insert.json \
  '.[] | {id, status: .result.errors // "SUCCESS"}'

echo "==> GET /v1/nodes/Landmark?output=verbose: shard -> replicas (factor 3 on 3 nodes: every node holds every shard)"
placement $N1
expect "$(placement $N1 | jq -s 'length')" 3 "shards"
expect "$(placement $N1 | jq -sc 'map(.replicas | length) | unique')" "[3]" "replicas per shard"
echo

echo "==> read one object by id at each consistency level, from each node"
for n in $N1 $N2 $N3; do for cl in ONE QUORUM ALL; do printf '%s: ' "${n#http://}"; get $n $cl; done; done
echo
for cl in ONE QUORUM ALL; do near $N2 $cl; done
expect "$(count $N3)" 6 "objects (Aggregate via weaviate-3)"
