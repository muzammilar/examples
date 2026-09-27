#!/usr/bin/env bash
# Shows discovered members, creates the schema, inserts on two shards and reads back through the
# Distributed tables. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib.sh
q() { echo "==> ${2:-clickhouse-server-01} < sql/$1"; ch "${2:-clickhouse-server-01}" --format PrettyCompact < "sql/$1"; }

# discovery is asynchronous: wait until the 9 data nodes from docker-compose.yml have registered
has_members() { [ "$(members | wc -l)" -ge 9 ]; }
wait_until 60 "9 members in cluster_hits" has_members
q 0-discovery.sql
./scripts/schema.sh
q 4-insert.sql clickhouse-server-01   # lands on shard 001, replicated to its other replicas
q 4-insert.sql clickhouse-server-05   # lands on shard 002
echo "==> rows per replica (test.test_table_local)"
while read -r shard host <&3; do
  ch "$host" -q "SYSTEM SYNC REPLICA test.test_table_local"
  printf 'shard %s  %-22s %s\n' "$shard" "$host" "$(ch "$host" -q 'SELECT count() FROM test.test_table_local')"
done 3< <(members)
echo "==> through the Distributed table"
ch clickhouse-server-01 --format PrettyCompact -q 'SELECT count() AS rows, uniq(val) AS distinct_vals FROM test.test_table'
