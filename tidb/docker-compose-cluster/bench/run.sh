#!/bin/sh
# `make benchmark`, part 1 (bench service, bench/Dockerfile: go-tpc built from source +
# sysbench from Debian), all through TiDB's MySQL protocol port on the compose network.
#   go-tpc TPC-C: WAREHOUSES warehouses loaded into database tpcc (prepare, timed), then the
#   TPC-C mix (new-order, payment, order-status, delivery, stock-level; no keying/think time)
#   for DURATION s with THREADS connections.
#   sysbench: TABLES tables of TABLE_SIZE rows in database sbtest, then oltp_point_select and
#   oltp_read_write for DURATION s each with THREADS threads.
# Everything goes to /results/$NAME.txt; bench/report.py (bench-report service) turns it into
# the summary table and JSON. Both databases are dropped at the end, also on failure.
set -eu

: "${NAME:?}" "${DURATION:?}" "${THREADS:?}" "${WAREHOUSES:?}" "${TABLES:?}" "${TABLE_SIZE:?}"
RAW=/results/$NAME.txt
TPC="go-tpc tpcc -H tidb -P 4000 -U root -D tpcc --warehouses $WAREHOUSES"
SB="sysbench --db-driver=mysql --mysql-host=tidb --mysql-port=4000 --mysql-user=root"
OLTP="--mysql-db=sbtest --tables=$TABLES --table-size=$TABLE_SIZE"

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
	echo "==> cleanup: DROP DATABASE tpcc, sbtest"
	$SB --mysql-db=mysql /bench/db.lua cleanup >>"$RAW" 2>&1 || true
}

: >"$RAW"
meta sysbench_version "$(sysbench --version | sed 's/^sysbench //')"
meta go_tpc_version "$GO_TPC_VERSION"
meta duration_s "$DURATION"
meta threads "$THREADS"
meta warehouses "$WAREHOUSES"
meta tables "$TABLES"
meta table_size "$TABLE_SIZE"
sb "CREATE DATABASE sbtest" --mysql-db=mysql /bench/db.lua prepare
trap cleanup EXIT

echo "==> go-tpc tpcc prepare: $WAREHOUSES warehouses"
start=$(date +%s.%N)
$TPC prepare -T "$THREADS" --dropdata >>"$RAW" 2>&1 || fail "go-tpc prepare"
echo "prepare: tpcc seconds=$(elapsed "$start")" >>"$RAW"

w=tpcc
echo "==> $w: $THREADS threads, $DURATION s"
echo "=== workload $w: go-tpc tpcc --warehouses $WAREHOUSES run -T $THREADS --time ${DURATION}s" >>"$RAW"
$TPC run -T "$THREADS" --time "${DURATION}s" --interval "${DURATION}s" >>"$RAW" 2>&1 || fail "go-tpc run"

echo "==> sysbench prepare: $TABLES tables x $TABLE_SIZE rows"
start=$(date +%s.%N)
sb "sysbench prepare" $OLTP --threads="$TABLES" oltp_read_write prepare
echo "prepare: sysbench seconds=$(elapsed "$start")" >>"$RAW"

for w in oltp_point_select oltp_read_write; do
	echo "==> $w: $THREADS threads, $DURATION s"
	echo "=== workload $w: sysbench $w --threads=$THREADS --time=$DURATION" >>"$RAW"
	# 8002/8022/9007: TiDB's write-conflict / retry errors, 1213/1205: deadlock / lock wait
	# timeout; sysbench retries the transaction and counts them as "ignored errors"
	sb "$w" $OLTP --threads="$THREADS" --time="$DURATION" --report-interval=0 --percentile=99 \
		--histogram=on --mysql-ignore-errors=8002,8022,9007,1213,1205 "$w" run
done
