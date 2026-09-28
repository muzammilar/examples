#!/bin/sh
# Phases, run by `make failover` in this order:
#   down       weaviate-3 is stopped: QUORUM (2 of 3 replicas) writes and reads work
#   up         weaviate-3 is back: wait for 3 healthy nodes; it catches up (read repair + async replication)
#   partition  weaviate-3 runs but rejects replica traffic (data port 7101): QUORUM works, ALL fails
#   heal       the port is open again: 3 healthy nodes, ALL works
set -eu
. scripts/lib.sh
ID7=00000000-0000-0000-0000-000000000007
ID9=00000000-0000-0000-0000-000000000009
ID10=00000000-0000-0000-0000-000000000010

healthy() { curl -sS $N1/v1/nodes | jq '[.nodes[] | select(.status == "HEALTHY")] | length'; }

wait_healthy() { # N SECONDS
  i=0
  until [ "$(healthy)" = "$1" ]; do
    i=$((i + 1)); [ $i -le "$2" ] || { echo "FAIL: not $1 healthy nodes after $2 s"; nodes $N1; exit 1; }; sleep 1
  done
  echo "$1 healthy nodes after ${i} s"
  nodes $N1
}

must_fail() { # WHAT CMD...: the command must fail
  what=$1; shift
  if "$@"; then echo "FAIL: $what succeeded"; exit 1; else echo "ok: $what rejected, as expected"; fi
}

case "$1" in
down)
  echo "==> waiting until weaviate-1 sees only 2 healthy nodes (a stopped node leaves memberlist)"
  wait_healthy 2 30
  echo
  batch "$N1/v1/batch/objects?consistency_level=QUORUM" requests/03-batch-insert-during-failover.json
  expect "$BATCH_ERRORS" 0 "failed objects in the QUORUM batch"
  get $N1 QUORUM $ID7
  get $N2 QUORUM
  near $N1 QUORUM
  echo
  # weaviate 1.31+ resolves the level against the replicas memberlist still knows; node3 left it
  # on shutdown, so ALL is computed over 2 replicas (weaviate/weaviate#13302). Shown, not asserted:
  echo "==> ALL after node3 left memberlist (2 known replicas; see README):"
  get $N1 ALL || true
  ;;
up)
  echo "==> waiting until all three nodes are HEALTHY"
  wait_healthy 3 120
  echo
  echo "==> ALL works again, also for an object weaviate-3 missed while it was down"
  get $N3 ALL $ID7
  get $N1 ALL
  echo "==> waiting until weaviate-3 has every object (async replication, on by default)"
  i=0
  until [ "$(count $N3)" = 8 ]; do
    i=$((i + 1)); [ $i -le 60 ] || { echo "FAIL: weaviate-3 has $(count $N3) objects after 120 s"; exit 1; }; sleep 2
  done
  for n in $N1 $N2 $N3; do expect "$(count $n)" 8 "objects (Aggregate via ${n#http://})"; done
  ;;
partition)
  echo "==> weaviate-3 is still a memberlist member but rejects its data port: 3 replicas, 2 reachable"
  nodes $N1
  echo
  must_fail "ALL read by id" get $N1 ALL
  batch "$N1/v1/batch/objects?consistency_level=ALL" requests/04-batch-insert-all.json
  expect "$BATCH_ERRORS" 1 "failed objects in the ALL batch"
  echo
  get $N1 QUORUM
  batch "$N1/v1/batch/objects?consistency_level=QUORUM" requests/05-batch-insert-partition.json
  expect "$BATCH_ERRORS" 0 "failed objects in the QUORUM batch"
  get $N2 QUORUM $ID10
  echo "==> search at ALL is not enforced by Weaviate (see README); shown, not asserted:"
  near $N1 ALL || true
  ;;
heal)
  echo "==> waiting until all three nodes are HEALTHY again"
  wait_healthy 3 60
  echo
  get $N1 ALL
  get $N3 ALL $ID10
  get $N1 ONE $ID9 || true
  expect "$(cat /tmp/get.code)" 404 "HTTP status of $ID9 (the rejected ALL write was not applied)"
  ;;
*) echo "usage: failover.sh down|up|partition|heal"; exit 2 ;;
esac
