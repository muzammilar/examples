#!/usr/bin/env bash
# Shows discovered members, creates the schema ON CLUSTER, inserts on two shards and reads back
# through the Distributed tables. Safe to re-run (IF NOT EXISTS everywhere).
set -euo pipefail
q() { # FILE [SERVER]
  echo "==> clickhouse-server-${2:-01} < sql/$1"
  docker compose exec -T "clickhouse-server-${2:-01}" clickhouse-client --format PrettyCompact < "sql/$1"
}
# discovery is asynchronous: wait until all 9 data nodes have registered
for _ in $(seq 30); do
  n=$(docker compose exec -T clickhouse-server-01 clickhouse-client -q "SELECT count() FROM system.clusters WHERE cluster = 'cluster_hits'")
  [ "$n" -ge 9 ] && break; sleep 1
done
q 0-discovery.sql
q 1-schema-base-tables.sql >/dev/null
q 2-schema-distributed-tables.sql >/dev/null
q 3-insert.sql 01   # lands on shard 001, replicated to 04 and 07
q 3-insert.sql 05   # lands on shard 002, replicated to 02 and 08
echo "==> rows per replica (test.test_table_local)"
for s in 01 04 07 02 05 08 03 06 09; do
  printf 'clickhouse-server-%s  %s\n' "$s" \
    "$(docker compose exec -T "clickhouse-server-$s" clickhouse-client -q 'SELECT count() FROM test.test_table_local')"
done
echo "==> through the Distributed table"
docker compose exec -T clickhouse-server-01 clickhouse-client --format PrettyCompact \
  -q 'SELECT count() AS rows, uniq(val) AS distinct_vals FROM test.test_table'
