#!/usr/bin/env bash
# Remove a data node: stop it, wait for discovery to drop it, then delete its replica metadata in Keeper.
# Without the last step the other replicas keep its entry forever and keep old parts/logs around for it.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib.sh
NAME=${1:?usage: remove-node.sh NAME}
case $NAME in
  clickhouse-server-0[123]|clickhouse-server-1[01])
    echo "$NAME runs clickhouse-keeper; removing it would shrink the RAFT ensemble" >&2; exit 1 ;;
esac
SHARD_NUM=$(members | awk -v n="$NAME" '$2 == n {print $1}')
[ -n "$SHARD_NUM" ] || { echo "$NAME is not a member of cluster_hits" >&2; exit 1; }
PEER=$(members | awk -v s="$SHARD_NUM" -v n="$NAME" '$1 == s && $2 != n {print $2; exit}')
[ -n "$PEER" ] || { echo "$NAME is the last replica of shard $SHARD_NUM; its data would be lost" >&2; exit 1; }
SHARD=$(ch "$NAME" -q "SELECT getMacro('shard')")

echo "==> stopping $NAME (shard $SHARD); its Keeper session closes and the registration disappears"
docker stop "$NAME" >/dev/null
gone() { ! members | grep -qw "$NAME"; }
wait_until 60 "$NAME to leave cluster_hits" gone
docker rm "$NAME" >/dev/null
echo "==> $NAME left cluster_hits"

echo "==> dropping $NAME's replica metadata via $PEER"
ch "$PEER" -q "SYSTEM DROP REPLICA '$NAME'"   # every ReplicatedMergeTree table
for db in test test_mvs; do
  if ch "$PEER" -q "EXISTS DATABASE $db" | grep -q 1; then
    ch "$PEER" -q "SYSTEM DROP DATABASE REPLICA '$NAME' FROM SHARD '$SHARD' FROM DATABASE $db"
  fi
done
if ch "$PEER" -q "EXISTS TABLE test.test_table_local" | grep -q 1; then
  echo "==> replicas of test.test_table_local shard $SHARD left in Keeper: $(ch "$PEER" -q "SELECT arrayStringConcat(groupArray(name), ' ') FROM system.zookeeper WHERE path = '/clickhouse/tables/test.test_table_local/$SHARD/replicas'")"
fi
