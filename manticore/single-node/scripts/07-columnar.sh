#!/bin/sh
# Columnar storage and secondary indexes on generated data. Runs inside the manticore container
# (manticore-load and the mysql client ship with the image).
#   events_row  default row-wise attribute storage (attributes memory-mapped, read per row)
#   events_col  engine='columnar' (Manticore Columnar Library: attributes stored per column,
#               compressed, read in blocks; only the columns a query touches are read)
# Both get secondary indexes on their attributes by default (secondary_indexes, MCL).
set -eu
ROWS=${ROWS:-1000000}
SQL="mysql -h127.0.0.1 -P9306 --table"
schema='message text, service string, status int, latency_ms int, ts timestamp, bytes bigint'
for t in events_row events_col; do
	engine=$([ $t = events_col ] && echo "engine='columnar'" || echo "engine='rowwise'")
	echo "==> manticore-load: $ROWS rows into $t ($engine), 4 threads, batches of 10000"
	manticore-load --quiet --drop --batch-size=10000 --threads=4 --total="$ROWS" \
		--init="CREATE TABLE $t ($schema) $engine" \
		--load="INSERT INTO $t (id,message,service,status,latency_ms,ts,bytes) VALUES (<increment>,'<text/5/20>','svc<int/1/50>',<int/200/504>,<int/1/3000>,<int/1780000000/1790000000>,<int/100/100000>)"
	# write the RAM chunk to a disk chunk and merge chunks, so both tables are in their final on-disk form
	$SQL -e "FLUSH RAMCHUNK $t; OPTIMIZE TABLE $t OPTION sync=1, cutoff=1"
done

echo; echo "==> size: disk_bytes on disk, ram_bytes held in RAM (attributes, dictionaries, caches)"
for t in events_row events_col; do
	$SQL -e "SHOW TABLE $t STATUS" | awk -v t=$t -F'|' '/ (indexed_documents|disk_bytes|ram_bytes|disk_chunks) /{gsub(/ /,"",$2); gsub(/ /,"",$3); printf "%-11s %-18s %s\n", t, $2, $3}'
done

q() { # table, label, query; prints the plan (SHOW META 'index') and latency over 200 runs
	plan=$($SQL --skip-column-names -e "$3; SHOW META LIKE 'index'" | awk -F'|' '/index/{print $3}' | sed 's/^ *//;s/ *$//')
	lat=$(manticore-load --quiet --json --latency-histograms=0 --threads=1 --total=200 --load="$3" |
		awk -F': ' '/"p50"/{gsub(/,/,"",$2); p50=$2} /"p99"/{gsub(/,/,"",$2); p99=$2} END{printf "p50 %s ms, p99 %s ms", p50, p99}')
	printf '%-11s %-34s %-24s %s\n' "$1" "$2" "$lat" "$plan"
}
echo; echo "==> queries: 1 thread, 200 runs each; plan = SHOW META 'index' (how each filter was evaluated)"
for t in events_row events_col; do
	q $t "status=503 (0.3% of rows)" "SELECT id FROM $t WHERE status=503 LIMIT 10"
	q $t "same, NO_SecondaryIndex hint" "SELECT id FROM $t WHERE status=503 LIMIT 10 /*+ NO_SecondaryIndex(status) */"
	q $t "count where status>=500" "SELECT COUNT(*) FROM $t WHERE status>=500"
	q $t "avg(latency_ms) per service" "SELECT service, AVG(latency_ms) FROM $t GROUP BY service LIMIT 50"
	q $t "status>=500 and latency_ms>2900" "SELECT COUNT(*) FROM $t WHERE status>=500 AND latency_ms>2900"
done
echo; echo "==> the tables stay for exploration (make cli); make down removes them"
