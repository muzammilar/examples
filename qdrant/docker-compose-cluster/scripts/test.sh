#!/bin/sh
# Cluster view, a 3-shard / 2-replica collection, upsert, placement, search from every peer.
set -eu
. scripts/lib.sh

echo "==> GET /cluster on each peer: members, Raft role and leader"
for n in $N1 $N2 $N3; do cluster $n; done
expect "$(curl -sS $N1/cluster | jq '.result.peers | length')" 3 "peers"
expect "$(curl -sS $N1/cluster | jq '.result.raft_info.leader != null')" true "raft leader elected"
echo

curl -sS -X DELETE "$N1/collections/$COLL" >/dev/null   # start clean, so the test is repeatable
req PUT  "$N1/collections/$COLL"                    requests/01-create-collection.json '.result'
req PUT  "$N1/collections/$COLL/points?wait=true"   requests/02-upsert-points.json     '.result.status'

echo "==> GET /collections/$COLL/cluster: shard -> replicas (3 shards x 2 replicas over 3 peers)"
placement $N1
echo

echo "==> exact count from each peer (each one fans out to the shards it does not hold)"
for n in $N1 $N2 $N3; do expect "$(count $n)" 6 "count via ${n#http://}"; done
echo

req POST "$N2/collections/$COLL/points/query" requests/03-knn-search.json '.result.points[] | {id, score, city: .payload.city, name: .payload.name}'
