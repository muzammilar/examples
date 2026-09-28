#!/bin/bash
# `make benchmark`, part 1 (bench service, postgres:17-alpine image): pgbench TPC-B-like and
# select-only runs, then the analytic queries of bench/analytics.sql over the 3M generated orders.
# Everything is appended to /results/$NAME.txt (the Makefile writes the server facts first);
# bench/report.py (bench-report service) turns that file into the summary table and JSON.
set -euo pipefail

: "${NAME:?}" "${DURATION:?}" "${CLIENTS:?}" "${SCALE:?}" "${RUNS:?}" "${MAX_TRIES:?}"
RAW=/results/$NAME.txt
LOGS=$(mktemp -d)

meta() { echo "meta: $1=$2" >>"$RAW"; }
meta pgbench_version "$(pgbench --version | sed 's/^pgbench (PostgreSQL) //')"
meta duration_s "$DURATION"
meta clients "$CLIENTS"
meta scale "$SCALE"
meta max_tries "$MAX_TRIES"
meta analytic_runs "$RUNS"
meta isolation "$(psql -Atc 'SHOW transaction_isolation')"

# pgbench_accounts gets SCALE x 100k rows; CedarDB ignores pgbench's fillfactor (a WARNING) and VACUUM
echo "==> pgbench -i -s $SCALE"
echo "=== init: pgbench -i -s $SCALE" >>"$RAW"
pgbench -i -s "$SCALE" -q >>"$RAW" 2>&1 || { echo "pgbench -i failed, see results/$NAME.txt" >&2; exit 1; }

# One pgbench run: per-transaction logs (-l) give the latency percentiles, which pgbench
# itself does not print; failed transactions (after MAX_TRIES tries) are left out of them.
bench() {
	name=$1 clients=$2
	shift 2
	args=("$@" -c "$clients" -j "$clients" -T "$DURATION" -n --max-tries="$MAX_TRIES")
	echo "==> $name, $clients clients, ${DURATION} s"
	echo "=== run $name clients=$clients: pgbench ${args[*]}" >>"$RAW"
	rm -f "$LOGS"/*
	pgbench "${args[@]}" -l --log-prefix="$LOGS/tx" >>"$RAW" 2>&1 ||
		{ echo "pgbench failed, see results/$NAME.txt" >&2; exit 1; }
	# column 3 of a pgbench log line is the latency in microseconds (or "failed")
	cat "$LOGS"/tx.* | awk '$3 != "failed" {print $3}' | sort -n | awk '
		function pct(p,  i) { i = int(p * NR); if (i < p * NR) i++; return v[i > 0 ? i : 1] / 1000 }
		{ v[NR] = $1; s += $1 }
		END { if (NR) printf "latency: n=%d avg_ms=%.3f p50_ms=%.3f p95_ms=%.3f p99_ms=%.3f max_ms=%.3f\n",
			NR, s / NR / 1000, pct(0.50), pct(0.95), pct(0.99), v[NR] / 1000; else print "latency: n=0" }' >>"$RAW"
}

for c in $CLIENTS; do bench tpcb "$c"; done
for c in $CLIENTS; do bench select-only "$c" -S; done

# Analytics on the same server: sql/01-generate.sql (100k customers, 3M orders), then every
# query of bench/analytics.sql RUNS times; psql prints "Time: ... ms" after each "query: <name>".
echo "==> generating 100k customers + 3M orders (sql/01-generate.sql)"
echo "=== load: sql/01-generate.sql" >>"$RAW"
psql -v ON_ERROR_STOP=1 -f /sql/01-generate.sql >>"$RAW" 2>&1 || { echo "load failed, see results/$NAME.txt" >&2; exit 1; }
for i in $(seq "$RUNS"); do
	echo "==> analytic queries, run $i/$RUNS"
	echo "=== analytics run=$i" >>"$RAW"
	psql -v ON_ERROR_STOP=1 -f /bench/analytics.sql >>"$RAW" 2>&1 || { echo "analytics failed, see results/$NAME.txt" >&2; exit 1; }
done
rm -rf "$LOGS"
