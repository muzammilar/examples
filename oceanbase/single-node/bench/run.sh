#!/bin/sh
# sysbench OLTP against the `test` tenant: prepare once, run each workload at each
# thread count for a fixed time, print a table, then drop the sbtest tables and database.
# Raw sysbench output and a JSON summary go to results/ (gitignored).
# Env (set by `make benchmark`): BENCH_TIME, BENCH_TABLES, BENCH_SIZE, BENCH_THREADS,
# BENCH_WORKLOADS.
set -eu
cd "$(dirname "$0")/.."

stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p results
raw=results/sysbench-$stamp.log
json=results/sysbench-$stamp.json

OBCLIENT="docker exec -i oceanbase obclient -h127.1 -P2881 -uroot@test -A -N -s"
OBSYS="docker exec -i oceanbase obclient -h127.1 -P2881 -uroot@sys -A -N -s"
SB="docker compose run --rm --no-deps -T sysbench"
common="--db-driver=mysql --mysql-host=oceanbase --mysql-port=2881 --mysql-user=root@test
	--mysql-password= --mysql-db=sbtest --tables=$BENCH_TABLES --table-size=$BENCH_SIZE"

cleanup() { $SB oltp_common $common cleanup >>"$raw" 2>&1 || true; }
trap 'cleanup; $OBCLIENT -e "DROP DATABASE IF EXISTS sbtest" || true; echo "sbtest removed"' EXIT

docker compose build --quiet sysbench
$OBCLIENT -e 'CREATE DATABASE IF NOT EXISTS sbtest'
cleanup # leftovers from an interrupted run
echo "prepare: $BENCH_TABLES tables x $BENCH_SIZE rows"
$SB oltp_common $common --threads="$BENCH_TABLES" prepare >>"$raw" 2>&1 ||
	{ tail -20 "$raw"; exit 1; }

rows=""
table=$(printf '%-18s %7s %9s %10s %8s %8s %6s' workload threads TPS QPS "avg ms" "p95 ms" errors)
for w in $BENCH_WORKLOADS; do
	for t in $BENCH_THREADS; do
		echo "run: $w, $t threads, ${BENCH_TIME}s"
		out=results/.run.$$
		$SB "$w" $common --threads="$t" --time="$BENCH_TIME" --report-interval=0 run >"$out" 2>&1 ||
			{ cat "$out" >>"$raw"; tail -20 "$out"; rm -f "$out"; exit 1; }
		{ echo "=== $w threads=$t"; cat "$out"; } >>"$raw"
		# line 1: JSON object, line 2: table row
		parsed=$(awk -v w="$w" -v t="$t" '
			/transactions:/    { tps = $3; sub(/^\(/, "", tps) }
			/^ *queries:/      { qps = $3; sub(/^\(/, "", qps) }
			/ignored errors:/  { err = $3 }
			/avg:/             { avg = $2 }
			/95th percentile:/ { p95 = $3 }
			END {
				printf "{\"workload\":\"%s\",\"threads\":%d,\"tps\":%s,\"qps\":%s,\"avg_ms\":%s,\"p95_ms\":%s,\"ignored_errors\":%s}\n", w, t, tps, qps, avg, p95, err
				printf "%-18s %7s %9s %10s %8s %8s %6s\n", w, t, tps, qps, avg, p95, err
			}' "$out")
		rm -f "$out"
		rows="${rows:+$rows,}$(echo "$parsed" | sed -n 1p)"
		table="$table
$(echo "$parsed" | sed -n 2p)"
	done
done

sysbench_version=$($SB --version 2>/dev/null | tr -d '\r' | sed 's/^sysbench //')
ob_version=$($OBCLIENT -e 'SELECT ob_version()' | tr -d '\r')
ob_image=$(docker inspect --format '{{.Config.Image}}' oceanbase)
unit=$($OBSYS -e "SELECT CONCAT(c.MAX_CPU, ' CPU, ', ROUND(c.MEMORY_SIZE/1024/1024/1024,1), ' GiB')
	FROM oceanbase.DBA_OB_TENANTS t JOIN oceanbase.DBA_OB_RESOURCE_POOLS p ON p.TENANT_ID = t.TENANT_ID
	JOIN oceanbase.DBA_OB_UNIT_CONFIGS c ON c.UNIT_CONFIG_ID = p.UNIT_CONFIG_ID WHERE t.TENANT_NAME = 'test'" 2>/dev/null | tr -d '\r' || true)
cpus=$(docker info --format '{{.NCPU}}')
mem=$(docker info --format '{{.MemTotal}}')

cat >"$json" <<JSON
{
  "timestamp": "$stamp",
  "system": "oceanbase-single-node",
  "versions": {"sysbench": "$sysbench_version", "oceanbase": "$ob_version", "image": "$ob_image"},
  "parameters": {"time_s": $BENCH_TIME, "tables": $BENCH_TABLES, "table_size": $BENCH_SIZE,
                 "threads": "$BENCH_THREADS", "tenant": "test", "tenant_unit": "$unit"},
  "machine": {"docker_cpus": $cpus, "docker_mem_bytes": $mem, "host": "$(uname -sm)"},
  "results": [$rows]
}
JSON

echo
echo "sysbench $sysbench_version -> OceanBase $ob_version, tenant test ($unit)"
echo "$BENCH_TABLES tables x $BENCH_SIZE rows, ${BENCH_TIME}s per run, Docker VM: $cpus CPUs, $((mem / 1073741824)) GiB"
echo "$table"
echo
echo "raw: $raw  json: $json"
