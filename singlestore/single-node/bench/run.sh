#!/bin/sh
# `make benchmark`, part 1 (bench service, bench/Dockerfile: sysbench from Debian), over the
# MySQL protocol to the master aggregator (singlestore:3306) on the compose network.
#   sysbench OLTP: TABLES tables of TABLE_SIZE rows in database sbtest, created as TABLE_TYPE
#   (rowstore: in-memory skiplist indexes; columnstore: universal storage on disk), then
#   oltp_point_select, oltp_read_only and oltp_read_write, DURATION s each with THREADS threads,
#   keys picked with RAND_TYPE (default uniform, see the README: with sysbench's default
#   "special" hot spot, read_write stalls on cross-partition lock cycles for lock_wait_timeout).
#   Columnstore analytics: bench/analytics.lua times a few aggregation queries RUNS times each
#   on the 600,000-row demo.orders columnstore table from sql/02-generate.sql.
# Everything goes to /results/$NAME.txt; bench/report.py (bench-report service) turns it into
# the summary table and JSON. The sbtest database is dropped at the end, also on failure.
set -eu

: "${NAME:?}" "${DURATION:?}" "${THREADS:?}" "${TABLES:?}" "${TABLE_SIZE:?}" "${TABLE_TYPE:?}" "${RAND_TYPE:?}" "${RUNS:?}"
: "${SINGLESTORE_PASSWORD:?}"
RAW=/results/$NAME.txt
SB="sysbench --db-driver=mysql --mysql-host=singlestore --mysql-port=3306 --mysql-user=root --mysql-password=$SINGLESTORE_PASSWORD"
OLTP="--mysql-db=sbtest --tables=$TABLES --table-size=$TABLE_SIZE --rand-type=$RAND_TYPE"

meta() { echo "meta: $1=$2" >>"$RAW"; }
fail() { echo "$1 failed, see results/$NAME.txt" >&2; exit 1; }
# sysbench exits 0 when a worker thread hits a FATAL error, so check its output as well
sb() {
	what=$1
	shift
	out=$(mktemp)
	$SB "$@" >"$out" 2>&1 || { cat "$out" >>"$RAW"; fail "$what"; }
	cat "$out" >>"$RAW"
	! grep -q '^FATAL' "$out" || fail "$what"
}
elapsed() { echo "$(date +%s.%N) $1" | awk '{printf "%.2f", $1 - $2}'; }
cleanup() {
	echo "==> cleanup: DROP DATABASE sbtest, default_table_type back to $RESTORE_TABLE_TYPE"
	$SB --mysql-db=information_schema /bench/db.lua cleanup >>"$RAW" 2>&1 || true
}

: >"$RAW"
meta sysbench_version "$(sysbench --version | sed 's/^sysbench //')"
meta duration_s "$DURATION"
meta threads "$THREADS"
meta tables "$TABLES"
meta table_size "$TABLE_SIZE"
meta table_type "$TABLE_TYPE"
meta rand_type "$RAND_TYPE"
meta runs "$RUNS"
RESTORE_TABLE_TYPE=columnstore
export RESTORE_TABLE_TYPE TABLE_TYPE RUNS
trap cleanup EXIT
sb "CREATE DATABASE sbtest" --mysql-db=information_schema /bench/db.lua prepare
RESTORE_TABLE_TYPE=$(sed -n 's/^meta: default_table_type_before=//p' "$RAW")

echo "==> sysbench prepare: $TABLES tables x $TABLE_SIZE rows, $TABLE_TYPE"
start=$(date +%s.%N)
sb "sysbench prepare" $OLTP --threads="$TABLES" oltp_read_write prepare
echo "prepare: sysbench seconds=$(elapsed "$start")" >>"$RAW"
sb "storage type" --mysql-db=information_schema /bench/db.lua storage

for w in oltp_point_select oltp_read_only oltp_read_write; do
	echo "==> $w: $THREADS threads, $DURATION s"
	echo "=== workload $w: sysbench $w --threads=$THREADS --time=$DURATION" >>"$RAW"
	sb "$w" $OLTP --threads="$THREADS" --time="$DURATION" --report-interval=0 --percentile=99 \
		--histogram=on "$w" run
done

echo "==> columnstore analytics: $RUNS runs per query on demo.orders"
echo "=== analytics: sysbench /bench/analytics.lua time --mysql-db=demo" >>"$RAW"
sb "analytics" --mysql-db=demo /bench/analytics.lua time
