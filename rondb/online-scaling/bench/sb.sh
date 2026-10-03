#!/bin/sh
# sysbench client for the scaling walkthrough (bench service; THREADS/TABLES/TABLE_SIZE from env)
#   sb.sh prepare                         CREATE DATABASE sbtest + TABLES x TABLE_SIZE rows, ENGINE=NDB
#   sb.sh run LABEL WORKLOAD SECONDS HOSTS  sysbench WORKLOAD against HOSTS (comma list: sysbench
#                                         spreads its connections over them round-robin), 5 s
#                                         interval reports, every error ignored and counted;
#                                         raw output in /results/LABEL.txt, one summary line on stdout
#   sb.sh cleanup                         DROP DATABASE sbtest
set -eu
SB="sysbench --db-driver=mysql --mysql-port=3306 --mysql-user=rondb --mysql-password=rondb"
OLTP="--mysql-db=sbtest --tables=$TABLES --table-size=$TABLE_SIZE"

case $1 in
prepare)
	$SB --mysql-host=mysqld-1 --mysql-db=mysql /bench/db.lua prepare
	# sysbench exits 0 when a worker thread hits a FATAL error, so check its output as well
	out=$($SB --mysql-host=mysqld-1 $OLTP --mysql-storage-engine=ndbcluster --threads="$TABLES" oltp_read_write prepare 2>&1) ||
		{ echo "$out" | cut -c1-300; exit 1; }
	echo "$out" | grep -v '^FATAL' | cut -c1-300
	! echo "$out" | grep -q '^FATAL' || { echo "$out" | grep '^FATAL' | cut -c1-300; exit 1; }
	;;
run)
	label=$2 workload=$3 secs=$4 hosts=$5
	raw=/results/$label.txt
	echo "start_epoch=$(date +%s)" >"$raw"
	$SB --mysql-host="$hosts" $OLTP --threads="$THREADS" --time="$secs" --report-interval=5 \
		--percentile=99 --mysql-ignore-errors=all "$workload" run >>"$raw" 2>&1 || { tail "$raw"; exit 1; }
	! grep -q '^FATAL' "$raw" || { grep '^FATAL' "$raw" | head; exit 1; }
	awk -v label="$label" -v w="$workload" -v hosts="$hosts" '
		/transactions:/ { tps = $3; sub(/\(/, "", tps) }
		/queries:/ && !q { qps = $3; sub(/\(/, "", qps); q = 1 }
		/ignored errors:/ { err = $3 }
		/99th percentile:/ { p99 = $3 }
		/avg:/ { avg = $2 }
		END { printf "%-22s %-17s via %-18s tps %8.1f  qps %9.1f  avg %6.2f ms  p99 %7.2f ms  errors %s\n",
			label, w, hosts, tps, qps, avg, p99, err }' "$raw"
	;;
cleanup)
	$SB --mysql-host=mysqld-1 --mysql-db=mysql /bench/db.lua cleanup
	;;
*) echo "usage: sb.sh prepare | run LABEL WORKLOAD SECONDS HOSTS | cleanup" >&2; exit 2 ;;
esac
