#!/bin/bash
# `make benchmark`, part 1 (bench service, timescaledb 2.13.1-pg15 image: psql,
# timescaledb-parallel-copy). Against the access node an1:
#   1. generate DEVICES x DAYS x 1440 readings (one per device per minute) into a CSV
#   2. load it with WORKERS timescaledb-parallel-copy workers into three layouts:
#        local  - an ordinary hypertable on the access node only
#        rf1    - distributed over dn1..dn3, replication_factor 1
#        rf2    - distributed over dn1..dn3, replication_factor 2
#   3. run bench/queries.sql RUNS times on each
# Raw output goes to /results/$NAME.txt; bench/report.py turns it into a table and JSON.
set -euo pipefail

: "${NAME:?}" "${DEVICES:?}" "${DAYS:?}" "${WORKERS:?}" "${BATCH:?}" "${RUNS:?}"
RAW=/results/$NAME.txt
CSV=/tmp/readings.csv
ROWS=$((DEVICES * DAYS * 1440))
PSQL=(psql -v ON_ERROR_STOP=1 -X)

meta() { echo "meta: $1=$2" >>"$RAW"; }
sql() { "${PSQL[@]}" -Atc "$1" 2> >(grep -v -E 'is deprecated|^DETAIL:  Multi-node' >&2); }
log() { echo "==> $*"; }

: >"$RAW"
meta server_version "$(sql 'SELECT version()')"
meta timescaledb_version "$(sql "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")"
meta parallel_copy_version "$(timescaledb-parallel-copy --version 2>&1 | head -1)"
meta devices "$DEVICES"
meta days "$DAYS"
meta rows "$ROWS"
meta workers "$WORKERS"
meta batch "$BATCH"
meta runs "$RUNS"

log "generating $ROWS rows into a CSV"
sql "\\copy (SELECT t, d, round((20 + 5 * sin(extract(epoch FROM t) / 86400 * 2 * pi()) + (d % 7) * 0.5 + random() * 2)::numeric, 2) FROM generate_series(timestamptz '2026-01-01', timestamptz '2026-01-01' + interval '1 day' * $DAYS - interval '1 minute', interval '1 minute') AS t, generate_series(1, $DEVICES) AS d) TO '$CSV' CSV" >/dev/null

for layout in local rf1 rf2; do
	case $layout in
	local) create="SELECT create_hypertable('bench_readings', 'time', chunk_time_interval => interval '1 day')" ;;
	rf1) create="SELECT create_distributed_hypertable('bench_readings', 'time', 'device_id', chunk_time_interval => interval '1 day', replication_factor => 1)" ;;
	rf2) create="SELECT create_distributed_hypertable('bench_readings', 'time', 'device_id', chunk_time_interval => interval '1 day', replication_factor => 2)" ;;
	esac
	"${PSQL[@]}" -q 2> >(grep -v -E 'is deprecated|^DETAIL:  Multi-node' >&2) <<-EOF >/dev/null
		SET client_min_messages = warning;
		DROP TABLE IF EXISTS bench_readings;
		CREATE TABLE bench_readings (time timestamptz NOT NULL, device_id integer NOT NULL, temperature double precision);
		$create;
	EOF
	log "ingest into $layout: $WORKERS workers, batches of $BATCH"
	echo "=== ingest layout=$layout" >>"$RAW"
	start=${EPOCHREALTIME/./}
	timescaledb-parallel-copy --connection "host=$PGHOST user=$PGUSER password=$PGPASSWORD sslmode=disable" \
		--db-name "$PGDATABASE" --table bench_readings --file "$CSV" --workers "$WORKERS" --batch-size "$BATCH" \
		--reporting-period 0s >>"$RAW" 2>&1
	end=${EPOCHREALTIME/./}
	echo "elapsed_ms: $(((end - start) / 1000))" >>"$RAW"
	echo "count: $(sql 'SELECT count(*) FROM bench_readings')" >>"$RAW"
	echo "size_bytes: $(sql "SELECT sum(total_bytes) FROM hypertable_detailed_size('bench_readings')")" >>"$RAW"
	sql "ANALYZE bench_readings" >/dev/null
	for i in $(seq "$RUNS"); do
		log "queries on $layout, run $i/$RUNS"
		echo "=== queries layout=$layout run=$i" >>"$RAW"
		"${PSQL[@]}" -f /bench/queries.sql >>"$RAW" 2>&1
	done
done
rm -f "$CSV"
