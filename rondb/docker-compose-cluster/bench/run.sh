#!/bin/sh
# `make benchmark`, part 1 (bench service, bench/Dockerfile: sysbench + wrk from Debian).
#   sysbench OLTP against the MySQL Server: TABLES tables of TABLE_SIZE rows created
#   ENGINE=NDB (--mysql-storage-engine=ndbcluster), so every row lives in the data nodes'
#   memory, replicated to both; then oltp_point_select, oltp_read_only and oltp_read_write,
#   DURATION s each with THREADS client threads.
#   wrk against the REST API server: pk-reads of random sbtest1 rows, no SQL layer.
# Everything goes to /results/$NAME.txt; bench/report.py (bench-report service) turns it into
# the summary table and JSON. The sbtest database is dropped at the end.
set -eu

: "${NAME:?}" "${DURATION:?}" "${THREADS:?}" "${TABLES:?}" "${TABLE_SIZE:?}"
RAW=/results/$NAME.txt
SB="sysbench --db-driver=mysql --mysql-host=mysqld --mysql-port=3306 --mysql-user=rondb --mysql-password=rondb"
OLTP="--mysql-db=sbtest --tables=$TABLES --table-size=$TABLE_SIZE"
REST_URL=http://rest:4406/0.1.0/sbtest/sbtest1/pk-read

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
cleanup() {
	echo "==> sysbench cleanup, DROP DATABASE sbtest"
	$SB $OLTP oltp_read_write cleanup >>"$RAW" 2>&1 || true
	$SB --mysql-db=mysql /bench/db.lua cleanup >>"$RAW" 2>&1 || true
}

: >"$RAW"
meta sysbench_version "$(sysbench --version | sed 's/^sysbench //')"
meta wrk_version "$(wrk -v 2>&1 | head -n 1 | cut -d' ' -f2)"
meta duration_s "$DURATION"
meta threads "$THREADS"
meta tables "$TABLES"
meta table_size "$TABLE_SIZE"
sb "CREATE DATABASE sbtest" --mysql-db=mysql /bench/db.lua prepare
trap cleanup EXIT

echo "==> sysbench prepare: $TABLES tables x $TABLE_SIZE rows, ENGINE=ndbcluster"
start=$(date +%s.%N)
sb "sysbench prepare" $OLTP --mysql-storage-engine=ndbcluster --threads="$TABLES" oltp_read_write prepare
echo "prepare: seconds=$(echo "$(date +%s.%N) $start" | awk '{printf "%.2f", $1 - $2}')" >>"$RAW"

for w in oltp_point_select oltp_read_only oltp_read_write; do
	echo "==> $w: $THREADS threads, $DURATION s"
	echo "=== workload $w: sysbench $w --threads=$THREADS --time=$DURATION" >>"$RAW"
	sb "$w" $OLTP --threads="$THREADS" --time="$DURATION" --report-interval=0 --percentile=99 \
		--histogram=on "$w" run
done

w=rest_pk_read
t=$((THREADS < 2 ? THREADS : 2)) # wrk needs at least one connection per thread
echo "==> $w: wrk, $THREADS connections, $DURATION s"
echo "=== workload $w: wrk -t $t -c $THREADS -d ${DURATION}s --latency -s /bench/pk-read.lua $REST_URL" >>"$RAW"
TABLE_SIZE=$TABLE_SIZE wrk -t "$t" -c "$THREADS" -d "${DURATION}s" --latency -s /bench/pk-read.lua "$REST_URL" \
	>>"$RAW" 2>&1 || fail "$w"
