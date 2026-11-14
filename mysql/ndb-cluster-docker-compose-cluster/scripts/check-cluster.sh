#!/bin/sh
set -eu
for node in sql0 sql1;do
 ready=$(sh scripts/sql.sh "$node" -Nse "SHOW STATUS LIKE 'Ndb_number_of_ready_data_nodes'" | awk '{print $2}')
 [ "$ready" = 9 ] || { echo "$node sees $ready data nodes, expected 9" >&2;exit 1; }
 sh scripts/sql.sh "$node" -e "SELECT TABLE_NAME, ENGINE FROM information_schema.tables WHERE TABLE_SCHEMA='orders'; SELECT TABLE_NAME,COUNT(*) AS partitions FROM information_schema.partitions WHERE TABLE_SCHEMA='orders' GROUP BY TABLE_NAME"
 partitions=$(sh scripts/sql.sh "$node" -Nse "SELECT COUNT(*) FROM information_schema.partitions WHERE TABLE_SCHEMA='orders' AND TABLE_NAME='orders'")
 [ "$partitions" = 3 ] || { echo "NDB allocated $partitions partitions, expected 3" >&2;exit 1; }
done
left=$(sh scripts/sql.sh sql0 -Nse 'SELECT count(*),sum(op_id),sum(amount),sum(customer_id) FROM orders')
right=$(sh scripts/sql.sh sql1 -Nse 'SELECT count(*),sum(op_id),sum(amount),sum(customer_id) FROM orders')
[ "$left" = "$right" ] || { echo 'SQL frontends disagree' >&2;exit 1; }
docker compose exec -T mgm0 ndb_mgm --ndb-connectstring=mgm0:1186,mgm1:1186 -e show
docker compose exec -T mgm0 ndb_desc --ndb-connectstring=mgm0:1186,mgm1:1186 -d orders orders -p
