#!/bin/bash
# Tenant scale up / down: change the `test` tenant's unit spec in place with
# ALTER RESOURCE UNIT (all its units, in every zone, change at once; no data moves) and
# run the same sysbench steps at every size, so the throughput change is visible.
#   RESIZE_STEPS  CPU:MEMORY per step (default "1:1536M 3:1536M 1:1536M"; 1:1G stalls, see README)
#   RESIZE_TIME   seconds per sysbench run (default 30)
#   RESIZE_THREADS, RESIZE_WORKLOADS, RESIZE_TABLES, RESIZE_SIZE
# The unit is restored to its starting spec at the end. Output: a table, plus raw sysbench
# output and a JSON summary in results/.
set -euo pipefail
cd "$(dirname "$0")/.."

STEPS=${RESIZE_STEPS:-1:1536M 3:1536M 1:1536M}
TIME=${RESIZE_TIME:-30}
THREADS=${RESIZE_THREADS:-32}
WORKLOADS=${RESIZE_WORKLOADS:-oltp_point_select oltp_read_write}
TABLES=${RESIZE_TABLES:-4}
SIZE=${RESIZE_SIZE:-50000}

sys() { docker exec -i ob1 obclient -h127.1 -P2881 -uroot@sys -A --table -e "$1"; }
sysv() { docker exec -i ob1 obclient -h127.1 -P2881 -uroot@sys -A -N -s -e "$1" 2>/dev/null | tr -d '\r'; }
SB="docker compose run --rm --no-deps -T sysbench"
common="--db-driver=mysql --mysql-host=ob1 --mysql-port=2881 --mysql-user=root@test --mysql-password=
	--mysql-db=sbtest --tables=$TABLES --table-size=$SIZE"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p results
raw=results/resize-$stamp.log
json=results/resize-$stamp.json
orig=$(sysv "SELECT CONCAT(MAX_CPU, ':', MEMORY_SIZE) FROM oceanbase.DBA_OB_UNIT_CONFIGS WHERE NAME = 'test_unit'")

set_unit() { # set_unit CPU MEMORY: change the unit config, wait until every unit of `test` runs with it
	# growing right after a shrink can fail until the observers have released the memory:
	# ERROR 4624 ... MEMORY_SIZE resource is not enough to hold a new unit; retry
	local err="" tries=0
	until err=$(docker exec -i ob1 obclient -h127.1 -P2881 -uroot@sys -A -e \
		"ALTER RESOURCE UNIT test_unit MIN_CPU = $1, MAX_CPU = $1, MEMORY_SIZE = '$2'" 2>&1); do
		tries=$((tries + 1))
		[ $tries -le 120 ] && echo "$err" | grep -q 4624 || { echo "$err" >&2; return 1; }
		[ $tries -gt 1 ] || echo "    $err; retrying every second"
		sleep 1
	done
	[ $tries = 0 ] || echo "    applied after $tries retries"
	local bytes
	bytes=$(sysv "SELECT MEMORY_SIZE FROM oceanbase.DBA_OB_UNIT_CONFIGS WHERE NAME = 'test_unit'")
	for _ in $(seq 1 60); do
		# a user tenant's unit memory is split between it (1002) and its meta tenant (1001)
		[ "$(sysv "SELECT COUNT(*) FROM (SELECT SVR_IP, SUM(MEMORY_SIZE) AS m, MAX(MAX_CPU) AS c
			FROM oceanbase.GV\$OB_UNITS WHERE TENANT_ID IN (1001, 1002) GROUP BY SVR_IP) x
			WHERE m = $bytes AND c = $1")" = 3 ] && return 0
		sleep 1
	done
	echo "resize.sh: units did not take $1 CPU / $2" >&2
	return 1
}

cleanup() {
	$SB oltp_common $common cleanup >>"$raw" 2>&1 || true
	docker exec -i ob1 obclient -h127.1 -P2881 -uroot@test -A -e 'DROP DATABASE IF EXISTS sbtest' || true
	set_unit "${orig%%:*}" "$((${orig#*:} / 1048576))M" >/dev/null || echo "resize.sh: could not restore test_unit" >&2
	echo "sbtest removed, test_unit back to ${orig%%:*} CPU / $((${orig#*:} / 1073741824))G"
}
trap cleanup EXIT

docker compose build --quiet sysbench
docker exec -i ob1 obclient -h127.1 -P2881 -uroot@test -A -e 'DROP DATABASE IF EXISTS sbtest; CREATE DATABASE sbtest'
echo "prepare: $TABLES tables x $SIZE rows"
$SB oltp_common $common --threads="$TABLES" prepare >>"$raw" 2>&1 || { tail -20 "$raw"; exit 1; }

rows="" table=$(printf '%-9s %-18s %7s %9s %10s %8s %8s %6s' unit workload threads TPS QPS "avg ms" "p95 ms" errors)
for step in $STEPS; do
	cpu=${step%%:*} mem=${step#*:}
	t0=$(python3 -c 'import time; print(time.time())')
	set_unit "$cpu" "$mem"
	echo "==> test_unit: $cpu CPU, $mem memory (applied on all 3 units in $(python3 -c "import time; print('%.1f' % (time.time() - $t0))") s)"
	sys "SELECT SVR_IP, TENANT_ID, MAX_CPU, MIN_CPU, ROUND(MEMORY_SIZE/1073741824, 2) AS mem_gb
	  FROM oceanbase.GV\$OB_UNITS WHERE TENANT_ID IN (1001, 1002) AND SVR_IP = '172.28.12.11' ORDER BY TENANT_ID;
	SELECT SVR_IP, ROUND(MEMSTORE_LIMIT/1048576) AS memstore_limit_mb, ROUND(FREEZE_TRIGGER/1048576) AS freeze_trigger_mb
	  FROM oceanbase.GV\$OB_MEMSTORE WHERE TENANT_ID = 1002 AND SVR_IP = '172.28.12.11'"
	for w in $WORKLOADS; do
		out=results/.run.$$
		$SB "$w" $common --threads="$THREADS" --time="$TIME" --report-interval=0 --mysql-ignore-errors=all run >"$out" 2>&1 ||
			{ cat "$out" >>"$raw"; tail -20 "$out"; rm -f "$out"; exit 1; }
		{ echo "=== unit=$cpu:$mem $w threads=$THREADS"; cat "$out"; } >>"$raw"
		parsed=$(awk -v u="${cpu}C/$mem" -v w="$w" -v t="$THREADS" '
			/transactions:/    { tps = $3; sub(/^\(/, "", tps) }
			/^ *queries:/      { qps = $3; sub(/^\(/, "", qps) }
			/ignored errors:/  { err = $3 }
			/avg:/             { avg = $2 }
			/95th percentile:/ { p95 = $3 }
			END {
				printf "{\"unit\":\"%s\",\"workload\":\"%s\",\"threads\":%d,\"tps\":%s,\"qps\":%s,\"avg_ms\":%s,\"p95_ms\":%s,\"ignored_errors\":%s}\n", u, w, t, tps, qps, avg, p95, err
				printf "%-9s %-18s %7s %9s %10s %8s %8s %6s\n", u, w, t, tps, qps, avg, p95, err
			}' "$out")
		rm -f "$out"
		echo "$parsed" | sed -n 2p
		rows="${rows:+$rows,}$(echo "$parsed" | sed -n 1p)"
		table="$table
$(echo "$parsed" | sed -n 2p)"
	done
done

cat >"$json" <<JSON
{"timestamp": "$stamp", "system": "oceanbase-scale-out-in", "test": "resize",
 "parameters": {"time_s": $TIME, "threads": $THREADS, "tables": $TABLES, "table_size": $SIZE, "steps": "$STEPS", "host": "ob1"},
 "docker": {"cpus": $(docker info --format '{{.NCPU}}'), "mem_bytes": $(docker info --format '{{.MemTotal}}')},
 "observer_cpus": "$(docker inspect -f '{{.HostConfig.NanoCpus}}' ob1)",
 "results": [$rows]}
JSON
echo
echo "$table"
echo "raw: $raw  json: $json"
