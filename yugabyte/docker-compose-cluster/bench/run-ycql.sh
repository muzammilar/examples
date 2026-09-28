#!/bin/bash
# `make benchmark-ycql`, part 1 (bench-ycql service, eclipse-temurin JRE image): YugabyteDB's
# workload generator, yb-sample-apps (bench/yb-sample-apps.jar, fetched by the Makefile), with
# its CassandraKeyValue workload against YCQL on yb-1..3. Everything it prints is appended to
# /results/$NAME.txt (the Makefile writes the cluster facts first); bench/report_ycql.py
# (bench-ycql-report service) turns that file into the summary table and JSON.
#
# Table ybdemo_keyspace.cassandrakeyvalue (k varchar PRIMARY KEY, v blob), RF=3 from the
# universe. Keys are "<UUID>:<n>" for n in 0..KEYS-1, values VALUE_SIZE random bytes with a
# key prefix and checksum that every read verifies.
set -euo pipefail

: "${NAME:?}" "${DURATION:?}" "${THREADS:?}" "${KEYS:?}" "${VALUE_SIZE:?}" "${LOAD_THREADS:?}" "${SAMPLE_APPS_VERSION:?}"
RAW=/results/$NAME.txt
NODES=yb-1:9042,yb-2:9042,yb-3:9042
UUID=$(cat /proc/sys/kernel/random/uuid) # key prefix: the timed runs reuse the keys the load wrote

meta() { echo "meta: $1=$2" >>"$RAW"; }
meta java_version "$(java -version 2>&1 | head -n 1)"
meta sample_apps_version "$SAMPLE_APPS_VERSION"
meta workload CassandraKeyValue
meta duration_s "$DURATION"
meta threads "$THREADS"
meta keys "$KEYS"
meta value_size "$VALUE_SIZE"
meta load_threads "$LOAD_THREADS"
meta nodes "$NODES"
meta uuid "$UUID"

# One yb-sample-apps process per run. It logs a status line every 5 s (ops/s and mean latency per
# interval, cumulative op counts, uptime) and, with --output_json_metrics, cumulative latency
# statistics (mean, p99, max) of every operation. The YugabyteDB driver's partition-aware policy
# sends each statement to the tablet leader of its key. The count(*) it runs before each workload
# can take a few seconds on a large table, hence the longer read timeout.
app() {
	name=$1 threads=$2
	shift 2
	args=(--workload CassandraKeyValue --nodes "$NODES" --uuid "$UUID" --num_unique_keys "$KEYS"
		--value_size "$VALUE_SIZE" --cql_read_timeout_ms 30000 --output_json_metrics "$@")
	echo "==> $name, $threads threads"
	echo "=== run $name threads=$threads: yb-sample-apps ${args[*]}" >>"$RAW"
	java -Xmx1g -Dlog4j.configuration=file:/bench/log4j.properties -jar /bench/yb-sample-apps.jar "${args[@]}" >>"$RAW" 2>&1 ||
		{ echo "yb-sample-apps failed, see results/$NAME.txt" >&2; exit 1; }
}

# load, not timed: KEYS new keys (inserts), the table the timed runs work on
app load "$LOAD_THREADS" --num_writes "$KEYS" --num_threads_write "$LOAD_THREADS" --num_threads_read 0

# --max_written_key KEYS-1: every key already exists, so writes are updates of random keys and
# reads pick random keys among all of them
timed=(--max_written_key $((KEYS - 1)) --num_writes -1 --num_reads -1 --run_time "$DURATION")
for t in $THREADS; do
	app write "$t" "${timed[@]}" --num_threads_write "$t" --num_threads_read 0
done
for t in $THREADS; do
	app read "$t" "${timed[@]}" --read_only --num_threads_read "$t"
done
# half the threads read, half update (1 -> 1 + 1: zero writers would make the tool load 100k extra keys first)
for t in $THREADS; do
	w=$((t / 2)) r=$((t - t / 2))
	[ "$w" -gt 0 ] || w=1
	app mixed "$t" "${timed[@]}" --num_threads_write "$w" --num_threads_read "$r"
done
