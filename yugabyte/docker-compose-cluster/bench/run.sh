#!/bin/bash
# `make benchmark`, part 1 (bench service, yugabytedb/yugabyte image): ysql_bench TPC-B-like and
# select-only runs against the RF=3 cluster. The clients of each run are split over one
# ysql_bench process per node (yb-1, yb-2, yb-3), so every node's YSQL layer takes connections.
# Everything is appended to /results/$NAME.txt (the Makefile writes the cluster facts first);
# bench/report.py (bench-report service) turns that file into the summary table and JSON.
set -euo pipefail

: "${NAME:?}" "${DURATION:?}" "${CLIENTS:?}" "${SCALE:?}" "${MAX_TRIES:?}"
RAW=/results/$NAME.txt
NODES=(yb-1 yb-2 yb-3)
BENCH=/home/yugabyte/postgres/bin/ysql_bench
LOGS=$(mktemp -d)
export PGUSER=yugabyte PGDATABASE=yugabyte

meta() { echo "meta: $1=$2" >>"$RAW"; }
meta ysql_bench_version "$($BENCH --version)"
meta duration_s "$DURATION"
meta clients "$CLIENTS"
meta scale "$SCALE"
meta max_tries "$MAX_TRIES"
meta nodes "${NODES[*]}"
meta isolation "$(ysqlsh -h yb-1 -Atc 'SHOW transaction_isolation')"

# pgbench_accounts gets SCALE x 100k rows (hash-sharded, RF=3), loaded through yb-1
echo "==> ysql_bench -i -s $SCALE"
echo "=== init: ysql_bench -i -s $SCALE" >>"$RAW"
$BENCH -h yb-1 -i -s "$SCALE" -q >>"$RAW" 2>&1 || { echo "ysql_bench -i failed, see results/$NAME.txt" >&2; exit 1; }

# One run: CLIENTS split over the nodes (8 -> 3/3/2; 1 -> yb-1 only), all processes in parallel.
# Per-transaction logs (-l) give the latency percentiles over all of them, which ysql_bench itself
# does not print; failed transactions (after MAX_TRIES tries) are left out of them.
bench() {
	name=$1 clients=$2
	shift 2
	args=("$@" -T "$DURATION" -n --max-tries="$MAX_TRIES")
	echo "==> $name, $clients clients, ${DURATION} s"
	echo "=== run $name clients=$clients: ysql_bench ${args[*]} (per node: -h <node> -c <n> -j <n>)" >>"$RAW"
	rm -f "$LOGS"/*
	pids=()
	for i in "${!NODES[@]}"; do
		n=$((clients / ${#NODES[@]} + (i < clients % ${#NODES[@]} ? 1 : 0)))
		[ "$n" -gt 0 ] || continue
		$BENCH -h "${NODES[$i]}" -c "$n" -j "$n" "${args[@]}" -l --log-prefix="$LOGS/tx-$i" \
			>"$LOGS/out-$i" 2>&1 &
		pids+=($!)
	done
	ok=1
	for p in "${pids[@]}"; do wait "$p" || ok=0; done
	for i in "${!NODES[@]}"; do
		[ -f "$LOGS/out-$i" ] || continue
		echo "--- node ${NODES[$i]}" >>"$RAW"
		cat "$LOGS/out-$i" >>"$RAW"
	done
	[ $ok = 1 ] || { echo "ysql_bench failed, see results/$NAME.txt" >&2; exit 1; }
	# column 3 of a log line is the latency in microseconds (or "failed")
	cat "$LOGS"/tx-* | awk '$3 != "failed" {print $3}' | sort -n | awk '
		function pct(p,  i) { i = int(p * NR); if (i < p * NR) i++; return v[i > 0 ? i : 1] / 1000 }
		{ v[NR] = $1; s += $1 }
		END { if (NR) printf "latency: n=%d avg_ms=%.3f p50_ms=%.3f p95_ms=%.3f p99_ms=%.3f max_ms=%.3f\n",
			NR, s / NR / 1000, pct(0.50), pct(0.95), pct(0.99), v[NR] / 1000; else print "latency: n=0" }' >>"$RAW"
}

for c in $CLIENTS; do bench tpcb "$c"; done
for c in $CLIENTS; do bench select-only "$c" -S; done
rm -rf "$LOGS"
