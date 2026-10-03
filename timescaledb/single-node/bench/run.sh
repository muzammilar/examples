#!/bin/bash
# `make benchmark`, part 1 (bench service, timescaledb-ha image, which ships psql and
# timescaledb-parallel-copy):
#   1. generate DEVICES x DAYS x 1440 readings (one per device per minute) into a CSV
#   2. ingest it into a fresh hypertable once per WORKERS count with timescaledb-parallel-copy,
#      straight into the columnstore (direct compress) and into the rowstore
#   3. run bench/queries.sql RUNS times on the rowstore
#   4. convert every chunk to the columnstore (timed, sizes before/after), run the queries again
#   5. build an hourly continuous aggregate (timed) and run bench/cagg-queries.sql on it
# Everything is appended to /results/$NAME.txt; bench/report.py turns it into a table and JSON.
set -euo pipefail

: "${NAME:?}" "${DEVICES:?}" "${DAYS:?}" "${WORKERS:?}" "${BATCH:?}" "${RUNS:?}"
RAW=/results/$NAME.txt
CSV=/tmp/readings.csv
ROWS=$((DEVICES * DAYS * 1440))
PSQL=(psql -v ON_ERROR_STOP=1 -X)

meta() { echo "meta: $1=$2" >>"$RAW"; }
sql() { "${PSQL[@]}" -Atc "$1"; }
log() { echo "==> $*"; }

: >"$RAW"
meta server_version "$(sql 'SELECT version()')"
meta timescaledb_version "$(sql "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")"
meta parallel_copy_version "$(timescaledb-parallel-copy --version 2>&1 | head -1 | sed 's/^.*timescaledb-parallel-copy //')"
meta devices "$DEVICES"
meta days "$DAYS"
meta rows "$ROWS"
meta workers "$WORKERS"
meta batch "$BATCH"
meta runs "$RUNS"

# a daily temperature cycle plus noise, 2 decimals; humidity 1 decimal; a fixed start date
log "generating $ROWS rows into a CSV"
sql "\\copy (SELECT t, d, round((20 + 5 * sin(extract(epoch FROM t) / 86400 * 2 * pi()) + (d % 7) * 0.5 + random() * 2)::numeric, 2), round((40 + random() * 20)::numeric, 1) FROM generate_series(timestamptz '2026-01-01', timestamptz '2026-01-01' + interval '1 day' * $DAYS - interval '1 minute', interval '1 minute') AS t, generate_series(1, $DEVICES) AS d) TO '$CSV' CSV" >/dev/null
meta csv_mib "$(($(stat -c %s "$CSV") / 1048576))"

create_table() {
	"${PSQL[@]}" -q <<-'EOF'
		SET client_min_messages = warning;
		DROP MATERIALIZED VIEW IF EXISTS bench_hourly CASCADE;
		DROP TABLE IF EXISTS bench_readings;
		CREATE TABLE bench_readings (
		  time        timestamptz NOT NULL,
		  device_id   integer NOT NULL,
		  temperature double precision,
		  humidity    double precision
		) WITH (tsdb.hypertable, tsdb.partition_column = 'time', tsdb.chunk_interval = '1 day',
		        tsdb.segmentby = 'device_id', tsdb.orderby = 'time DESC');
		-- no automatic columnstore policy during the ingest runs
		CALL remove_columnstore_policy('bench_readings', if_exists => true);
		CREATE INDEX ON bench_readings (device_id, time DESC);
	EOF
}

# timescaledb-parallel-copy splits the CSV into BATCH-row COPY statements over N connections.
# By default it writes straight into the columnstore ("direct compress": rows are compressed
# in the COPY itself); with --disable-direct-compress they land in the rowstore, which also
# maintains the default time index and the (device_id, time DESC) index. Both modes run at every
# WORKERS count; the last rowstore run is the table the queries below use.
ingest() { # mode workers
	create_table
	log "ingest: timescaledb-parallel-copy into the $1, $2 workers, batches of $BATCH"
	echo "=== ingest mode=$1 workers=$2" >>"$RAW"
	start=$(date +%s.%N)
	timescaledb-parallel-copy --connection "host=$PGHOST user=$PGUSER password=$PGPASSWORD dbname=$PGDATABASE sslmode=disable" \
		--table bench_readings --file "$CSV" --workers "$2" --batch-size "$BATCH" --reporting-period 0s \
		$([ "$1" = rowstore ] && echo --disable-direct-compress) >>"$RAW" 2>&1
	end=$(date +%s.%N)
	echo "elapsed_s: $(awk "BEGIN { print $end - $start }")" >>"$RAW"
	echo "count: $(sql 'SELECT count(*) FROM bench_readings')" >>"$RAW"
	echo "size_bytes: $(sql "SELECT hypertable_size('bench_readings')")" >>"$RAW"
}
for w in $WORKERS; do ingest columnstore "$w"; done
for w in $WORKERS; do ingest rowstore "$w"; done
rm -f "$CSV"

sql "VACUUM ANALYZE bench_readings" >/dev/null
echo "=== rowstore" >>"$RAW"
echo "chunks: $(sql "SELECT count(*) FROM show_chunks('bench_readings')")" >>"$RAW"
echo "size_bytes: $(sql "SELECT hypertable_size('bench_readings')")" >>"$RAW"
echo "detail: $(sql "SELECT table_bytes, index_bytes FROM hypertable_detailed_size('bench_readings')")" >>"$RAW"

for i in $(seq "$RUNS"); do
	log "queries on the rowstore, run $i/$RUNS"
	echo "=== queries phase=rowstore run=$i" >>"$RAW"
	"${PSQL[@]}" -f /bench/queries.sql >>"$RAW" 2>&1
done

log "converting every chunk to the columnstore"
echo "=== convert" >>"$RAW"
"${PSQL[@]}" >>"$RAW" 2>&1 <<-'EOF'
	\timing on
	DO $$
	DECLARE c regclass;
	BEGIN
	  FOR c IN SELECT show_chunks('bench_readings') LOOP CALL convert_to_columnstore(c); END LOOP;
	END $$;
EOF
sql "VACUUM ANALYZE bench_readings" >/dev/null
echo "=== columnstore" >>"$RAW"
echo "size_bytes: $(sql "SELECT hypertable_size('bench_readings')")" >>"$RAW"
echo "stats: $(sql "SELECT before_compression_total_bytes, after_compression_total_bytes FROM hypertable_columnstore_stats('bench_readings')")" >>"$RAW"

for i in $(seq "$RUNS"); do
	log "queries on the columnstore, run $i/$RUNS"
	echo "=== queries phase=columnstore run=$i" >>"$RAW"
	"${PSQL[@]}" -f /bench/queries.sql >>"$RAW" 2>&1
done

# the continuous aggregate is built from the columnstore chunks
log "building the hourly continuous aggregate"
echo "=== cagg" >>"$RAW"
"${PSQL[@]}" >>"$RAW" 2>&1 <<-'EOF'
	\timing on
	CREATE MATERIALIZED VIEW bench_hourly WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
	SELECT time_bucket('1 hour', time) AS hour, device_id,
	       sum(temperature) AS sum_temp, count(*) AS n, max(temperature) AS max_temp, max(humidity) AS max_hum
	FROM bench_readings GROUP BY hour, device_id WITH NO DATA;
	CALL refresh_continuous_aggregate('bench_hourly', NULL, NULL);
EOF
echo "cagg_rows: $(sql 'SELECT count(*) FROM bench_hourly')" >>"$RAW"
for i in $(seq "$RUNS"); do
	log "queries on the continuous aggregate, run $i/$RUNS"
	echo "=== queries phase=cagg run=$i" >>"$RAW"
	"${PSQL[@]}" -f /bench/cagg-queries.sql >>"$RAW" 2>&1
done
