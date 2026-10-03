#!/bin/bash
# `make benchmark`, part 1 (bench service, timescaledb-ha image: psql, pgbench,
# timescaledb-parallel-copy). Everything goes through HAProxy: :5000 = primary, :5001 = replicas.
#   1. generate DEVICES x DAYS x 1440 readings (one per device per minute) into a CSV
#   2. for each replication mode in MODES (async, sync = Patroni synchronous_mode):
#      ingest the CSV with WORKERS timescaledb-parallel-copy workers into the rowstore and
#      straight into the columnstore; record rows/s, WAL bytes written and the time until both
#      replicas have replayed it
#   3. read scaling: a pgbench script (one device's last day, hourly) for DURATION seconds with
#      CLIENTS clients, against the primary alone and against the two replicas
# Raw output goes to /results/$NAME.txt; bench/report.py turns it into a table and JSON.
set -euo pipefail

: "${NAME:?}" "${DEVICES:?}" "${DAYS:?}" "${WORKERS:?}" "${BATCH:?}" "${MODES:?}"
CLIENTS=${CLIENTS:-8}
DURATION=${DURATION:-20}
RAW=/results/$NAME.txt
CSV=/tmp/readings.csv
ROWS=$((DEVICES * DAYS * 1440))
PSQL=(psql -v ON_ERROR_STOP=1 -X)

meta() { echo "meta: $1=$2" >>"$RAW"; }
sql() { "${PSQL[@]}" -Atc "$1"; }
log() { echo "==> $*"; }
now_ms() { date +%s%3N; }

: >"$RAW"
meta server_version "$(sql 'SELECT version()')"
meta timescaledb_version "$(sql "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")"
meta parallel_copy_version "$(timescaledb-parallel-copy --version 2>&1 | head -1 | sed 's/^.*timescaledb-parallel-copy //')"
meta patroni_version "$(curl -s http://tsdb1:8008/patroni | sed -n 's/.*"patroni": {"version": "\([^"]*\)".*/\1/p')"
meta devices "$DEVICES"
meta days "$DAYS"
meta rows "$ROWS"
meta workers "$WORKERS"
meta batch "$BATCH"
meta clients "$CLIENTS"
meta duration_s "$DURATION"

log "generating $ROWS rows into a CSV"
sql "\\copy (SELECT t, d, round((20 + 5 * sin(extract(epoch FROM t) / 86400 * 2 * pi()) + (d % 7) * 0.5 + random() * 2)::numeric, 2), round((40 + random() * 20)::numeric, 1) FROM generate_series(timestamptz '2026-01-01', timestamptz '2026-01-01' + interval '1 day' * $DAYS - interval '1 minute', interval '1 minute') AS t, generate_series(1, $DEVICES) AS d) TO '$CSV' CSV" >/dev/null

# Patroni's REST API: PATCH /config on any member changes the cluster-wide (etcd) config
set_mode() {
	local want=$([ "$1" = sync ] && echo true || echo false)
	curl -fs -XPATCH -d "{\"synchronous_mode\": $want}" http://tsdb1:8008/config >/dev/null
	for _ in $(seq 60); do
		n=$(sql "SELECT count(*) FROM pg_stat_replication WHERE sync_state IN ('sync', 'quorum')")
		{ [ "$1" = sync ] && [ "$n" -ge 1 ]; } || { [ "$1" = async ] && [ "$n" = 0 ]; } && return 0
		sleep 1
	done
	echo "replication mode $1 not reached" >&2
	exit 1
}

create_table() {
	"${PSQL[@]}" -q <<-'EOF'
		SET client_min_messages = warning;
		DROP TABLE IF EXISTS bench_readings;
		CREATE TABLE bench_readings (
		  time        timestamptz NOT NULL,
		  device_id   integer NOT NULL,
		  temperature double precision,
		  humidity    double precision
		) WITH (tsdb.hypertable, tsdb.partition_column = 'time', tsdb.chunk_interval = '1 day',
		        tsdb.segmentby = 'device_id', tsdb.orderby = 'time DESC');
		CALL remove_columnstore_policy('bench_readings', if_exists => true);
		CREATE INDEX ON bench_readings (device_id, time DESC);
	EOF
}

ingest() { # mode store
	create_table
	log "ingest ($1 replication) into the $2: $WORKERS workers, batches of $BATCH"
	echo "=== ingest mode=$1 store=$2" >>"$RAW"
	lsn0=$(sql 'SELECT pg_current_wal_lsn()')
	start=$(now_ms)
	timescaledb-parallel-copy --connection "host=$PGHOST port=$PGPORT user=$PGUSER password=$PGPASSWORD dbname=$PGDATABASE sslmode=disable" \
		--table bench_readings --file "$CSV" --workers "$WORKERS" --batch-size "$BATCH" --reporting-period 0s \
		$([ "$2" = rowstore ] && echo --disable-direct-compress) >>"$RAW" 2>&1
	end=$(now_ms)
	lsn1=$(sql 'SELECT pg_current_wal_lsn()')
	# wait until both replicas have replayed everything the ingest wrote
	until [ "$(sql "SELECT count(*) FROM pg_stat_replication WHERE replay_lsn >= '$lsn1'")" -ge 2 ]; do sleep 0.05; done
	caught=$(now_ms)
	echo "elapsed_ms: $((end - start))" >>"$RAW"
	echo "replica_catchup_ms: $((caught - end))" >>"$RAW"
	echo "wal_bytes: $(sql "SELECT pg_wal_lsn_diff('$lsn1', '$lsn0')")" >>"$RAW"
	echo "count: $(sql 'SELECT count(*) FROM bench_readings')" >>"$RAW"
	echo "size_bytes: $(sql "SELECT hypertable_size('bench_readings')")" >>"$RAW"
}

for mode in $MODES; do
	set_mode "$mode"
	ingest "$mode" rowstore
	ingest "$mode" columnstore
done
set_mode async
rm -f "$CSV"
sql "ANALYZE bench_readings" >/dev/null

# read scaling on the last (columnstore) table: same query, primary alone vs the two replicas
cat >/tmp/read.sql <<-'EOF'
	\set d random(1, :devices)
	SELECT time_bucket('1 hour', time) AS hour, avg(temperature), max(humidity)
	FROM bench_readings WHERE device_id = :d AND time >= :since::timestamptz GROUP BY hour;
EOF
since="'$(sql "SELECT (max(time) - interval '1 day')::text FROM bench_readings")'"
for target in primary:5000 replicas:5001; do
	log "reads on the ${target%%:*} (HAProxy :${target#*:}): $CLIENTS clients, $DURATION s"
	echo "=== reads target=${target%%:*}" >>"$RAW"
	PGPORT=${target#*:} pgbench -n -f /tmp/read.sql -D devices="$DEVICES" -D since="$since" \
		-c "$CLIENTS" -j "$CLIENTS" -T "$DURATION" >>"$RAW" 2>&1
done
