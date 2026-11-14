#!/bin/sh
set -eu
for pair in '0:100';do
 shard=${pair%:*};first=${pair#*:}
 primary=$(sh scripts/primary.sh "$shard")
 gtid=$(sh scripts/sql.sh "$primary" -Nse 'SELECT @@GLOBAL.gtid_executed')
 expected=$(sh scripts/sql.sh "$primary" -Dvt_orders -Nse 'SELECT count(*),sum(op_id),sum(amount),sum(customer_id) FROM orders')
 for offset in 0 1 2;do
  node=t$((first+offset))
  if [ "$node" != "$primary" ];then
   result=$(sh scripts/sql.sh "$node" -Nse "SELECT WAIT_FOR_EXECUTED_GTID_SET('$gtid',60)")
   [ "$result" = 0 ] || { echo "$node failed to catch up to primary GTID" >&2;exit 1; }
  fi
  observed=$(sh scripts/sql.sh "$node" -Dvt_orders -Nse 'SELECT count(*),sum(op_id),sum(amount),sum(customer_id) FROM orders')
  [ "$observed" = "$expected" ] || { echo "$node differs from $primary" >&2;exit 1; }
  printf '%s shard=%s matches=%s\n' "$node" "$shard" "$observed"
 done
done
