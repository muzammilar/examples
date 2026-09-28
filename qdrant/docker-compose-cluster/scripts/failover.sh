#!/bin/sh
# failover.sh down: qdrant-3 is stopped; the cluster still serves reads and writes from the
#                   surviving replicas and records the dead peer
# failover.sh up:   qdrant-3 is back; wait until its replicas have caught up (Active again)
set -eu
. scripts/lib.sh

case "$1" in
down)
  echo "==> waiting until qdrant-1 fails to reach qdrant-3 (Raft message_send_failures)"
  i=0
  until curl -sS $N1/cluster | jq -e '.result.message_send_failures | keys | any(test("qdrant-3"))' >/dev/null; do
    i=$((i + 1)); [ $i -le 30 ] || { echo "FAIL: qdrant-3 not reported unreachable"; exit 1; }; sleep 1
  done
  cluster $N1
  expect "$(curl -sS $N1/cluster | jq '.result.raft_info.leader != null')" true "raft leader (2 of 3 peers form a majority)"
  echo

  req POST "$N1/collections/$COLL/points/query" requests/03-knn-search.json '.result.points[] | {id, score, city: .payload.city, name: .payload.name}'
  expect "$(count $N1)" 6 "count via qdrant-1 (every shard still has a live replica)"
  echo

  # write_consistency_factor 1: an update succeeds once one replica of each shard has applied it;
  # the replicas on qdrant-3 miss it and are marked Dead through consensus
  req PUT "$N1/collections/$COLL/points?wait=true" requests/04-upsert-during-failover.json '.result.status'
  expect "$(count $N2)" 12 "count via qdrant-2"
  echo "==> GET /collections/$COLL/cluster: the replicas on qdrant-3 are Dead"
  placement $N1
  expect "$(curl -sS $N1/collections/$COLL/cluster | jq '[.result.remote_shards[] | select(.state == "Dead")] | length > 0')" true "replicas on qdrant-3 marked Dead"
  ;;
up)
  echo "==> waiting until every replica is Active again (qdrant-3 recovers its shards by transfer)"
  i=0
  until all_active $N1 && all_active $N3; do
    i=$((i + 1)); [ $i -le 60 ] || { echo "FAIL: replicas not Active after 120 s"; placement $N1; exit 1; }; sleep 2
  done
  echo "all Active after $((i * 2)) s of polling"
  placement $N1
  cluster $N3
  for n in $N1 $N2 $N3; do expect "$(count $n)" 12 "count via ${n#http://}"; done
  ;;
*) echo "usage: failover.sh down|up"; exit 2 ;;
esac
