#!/bin/sh
set -eu
: > ${RESULTS_DIR:-results/manual}/customer-placement.tsv
for shard in -55 55-aa aa-;do
 primary=$(sh scripts/primary.sh "$shard")
 sh scripts/sql.sh "$primary" -Dvt_orders -Nse 'SELECT customer_id FROM customers ORDER BY customer_id' |
 awk -v shard="$shard" '{print $1,shard}' >> ${RESULTS_DIR:-results/manual}/customer-placement.tsv
done
awk -v expected="${CUSTOMERS:-10000}" '{if($1<0||$1>=expected||seen[$1]++)bad=1;shards[$2]++;n++} END {for(s in shards)printf "shard=%s customers=%d\n",s,shards[s];if(bad||n!=expected)exit 1;printf "Verified %d customers, each on exactly one shard\n",n}' ${RESULTS_DIR:-results/manual}/customer-placement.tsv
