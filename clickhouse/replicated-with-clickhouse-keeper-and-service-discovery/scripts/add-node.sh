#!/usr/bin/env bash
# Add a data node at runtime: NAME joins shard SHARD of cluster_hits with no config change anywhere.
#   1. start it from the clickhouse-node template; it registers itself in Keeper (cluster discovery)
#   2. create the Replicated databases on it; they replay the schema from Keeper
#   3. its ReplicatedMergeTree tables fetch the shard's existing parts from the other replicas
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib.sh
NAME=${1:?usage: add-node.sh NAME SHARD}
SHARD=${2:?usage: add-node.sh NAME SHARD}
[[ $SHARD =~ ^[0-9]{3}$ ]] || { echo "SHARD must be three digits, e.g. 001" >&2; exit 1; }
if docker container inspect "$NAME" >/dev/null 2>&1; then echo "$NAME already exists" >&2; exit 1; fi

echo "==> starting $NAME (shard $SHARD)"
NODE_NAME=$NAME SHARD=$SHARD docker compose --profile nodes run --detach --name "$NAME" --use-aliases clickhouse-node >/dev/null
healthy() { [ "$(docker inspect -f '{{.State.Health.Status}}' "$NAME")" = healthy ]; }
wait_until 120 "$NAME healthy" healthy
joined() { members | grep -qw "$NAME"; }
wait_until 60 "$NAME in cluster_hits" joined
echo "==> $NAME registered itself in cluster_hits"

if ch clickhouse-server-01 -q "EXISTS DATABASE test" | grep -q 1; then
  # The new node has an empty disk, so any replica metadata already in Keeper under its name is stale
  # (a previous node with this name that went away without remove-node). Clear it from a shard peer,
  # otherwise CREATE DATABASE fails with REPLICA_ALREADY_EXISTS.
  PEER=$(members | awk -v s="$((10#$SHARD))" -v n="$NAME" '$1 == s && $2 != n {print $2; exit}')
  if [ -n "$PEER" ]; then
    for db in test test_mvs; do
      if ch "$PEER" -q "SELECT count() FROM system.zookeeper WHERE path = '/clickhouse/databases/$db/replicas' AND name = '$SHARD|$NAME'" | grep -q 1; then
        echo "==> clearing stale $db replica '$SHARD|$NAME' via $PEER"
        ch "$PEER" -q "SYSTEM DROP DATABASE REPLICA '$NAME' FROM SHARD '$SHARD' FROM DATABASE $db"
      fi
    done
    ch "$PEER" -q "SYSTEM DROP REPLICA '$NAME'" 2>/dev/null || true   # table replicas; no-op if none
  fi
  echo "==> creating Replicated databases on $NAME; schema replays from Keeper"
  ch "$NAME" < sql/1-databases.sql
  ch "$NAME" -q "SYSTEM SYNC DATABASE REPLICA test"
  ch "$NAME" -q "SYSTEM SYNC DATABASE REPLICA test_mvs"
  ch "$NAME" -q "SYSTEM SYNC REPLICA test.test_table_local"
  ch "$NAME" -q "SYSTEM SYNC REPLICA test.test_table_hourly_smt_local"
  echo "==> $NAME tables: $(ch "$NAME" -q "SELECT arrayStringConcat(groupArray(database || '.' || name), ' ') FROM system.tables WHERE database IN ('test', 'test_mvs')")"
  echo "==> $NAME rows in test.test_table_local: $(ch "$NAME" -q 'SELECT count() FROM test.test_table_local')"
else
  echo "==> no schema yet (run make test); $NAME only joined the cluster"
fi
