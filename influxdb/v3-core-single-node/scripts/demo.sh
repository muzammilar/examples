#!/bin/bash
# `make test`: runs in the `client` service (the influxdb image: influxdb3 CLI + curl) against
# http://influxdb3:8181. Recreates the `home` database each run, so it can be repeated.
set -euo pipefail

TOKEN=$(sed -n 's/.*"token": *"\([^"]*\)".*/\1/p' /token/admin.json) # offline admin token
export INFLUXDB3_AUTH_TOKEN=$TOKEN INFLUXDB3_HOST_URL=${INFLUXDB3_HOST_URL:-http://influxdb3:8181}
URL=$INFLUXDB3_HOST_URL
DB=home
AUTH="Authorization: Bearer $TOKEN"

step() { printf '\n==== %s\n' "$*"; }
sql() { influxdb3 query --database "$DB" "$1"; } # SQL through the CLI (Flight SQL)
http() { # METHOD PATH [curl args...]: prints the body and the status code
	local m=$1 p=$2
	shift 2
	curl -sS -X "$m" -H "$AUTH" -w ' -> HTTP %{http_code}\n' "$URL$p" "$@"
}

step "1. auth: requests need a token (the server loaded an offline admin token from /token/admin.json)"
curl -sS -o /dev/null -w 'no token     -> HTTP %{http_code}\n' "$URL/api/v3/configure/database?format=json"
curl -sS -o /dev/null -w 'admin token  -> HTTP %{http_code}\n' -H "$AUTH" "$URL/api/v3/configure/database?format=json"
influxdb3 delete token --token-name demo-ops --yes >/dev/null 2>&1 || true
influxdb3 create token --admin --name demo-ops --expiry 1h --format json |
	sed -n 's/.*"token": *"\([^"]*\)".*/named admin token demo-ops (expires in 1h): \1/p' | cut -c1-60 | sed 's/$/.../'
influxdb3 show tokens | cut -c1-120
influxdb3 delete token --token-name demo-ops --yes

step "2. database $DB (dropped and recreated), retention 30d"
influxdb3 delete database "$DB" --hard-delete now --yes >/dev/null 2>&1 || true
influxdb3 create database "$DB" --retention-period 30d
influxdb3 show databases

step "3. line protocol: 30 minutes of readings, 3 rooms x 1/min, POST /api/v3/write_lp?precision=second"
now=$(date +%s)
lp=$(awk -v now="$now" 'BEGIN {
	split("Kitchen,Living\\ Room,Office", rooms, ",")
	for (m = 30; m >= 1; m--)
		for (r = 1; r <= 3; r++) {
			t = 20 + r + 1.5 * sin((m + 7 * r) / 5)
			printf "home,room=%s temp=%.1f,hum=%.1f,co=%di %d\n", rooms[r], t, 34 + r + (m % 4) * 0.3, (m < 8 && r == 1) ? 30 - 3 * m : 0, now - 60 * m
		}
}')
echo "$lp" | head -3
echo "... $(echo "$lp" | wc -l) lines"
http POST "/api/v3/write_lp?db=$DB&precision=second" --data-binary "$lp"
echo "v1 /write and v2 /api/v2/write accept the same line protocol:"
http POST "/write?db=$DB&precision=s" --data-binary "home,room=Garage temp=15.2,hum=50.1,co=0i $((now - 30))"
http POST "/api/v2/write?bucket=$DB&precision=s" --data-binary "home,room=Garage temp=15.4,hum=50.0,co=0i $((now - 20))"
echo "a bad line is rejected, the good lines of the same request are kept (accept_partial):"
http POST "/api/v3/write_lp?db=$DB&precision=second" --data-binary "home,room=Garage temp=15.5,hum=49.9,co=0i $((now - 10))
home,room=Garage temp=\"warm\" $((now - 10))"

step "4. SQL (POST /api/v3/query_sql): the schema is the line protocol (tags, fields, time)"
http POST /api/v3/query_sql -H 'Content-Type: application/json' \
	-d "{\"db\":\"$DB\",\"q\":\"SELECT room, count(*) AS n, round(avg(temp),2) AS avg_temp, max(co) AS max_co FROM home GROUP BY room ORDER BY room\",\"format\":\"pretty\"}"
sql "SELECT date_bin(INTERVAL '10 minutes', time) AS bucket, room, round(avg(temp), 2) AS avg_temp
     FROM home WHERE room IN ('Kitchen', 'Office') AND time >= now() - INTERVAL '1 hour'
     GROUP BY 1, 2 ORDER BY 1, 2"
sql "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'home'"

step "5. InfluxQL (GET /api/v3/query_influxql, and the v1 /query endpoint)"
http GET "/api/v3/query_influxql?db=$DB&format=pretty" --data-urlencode \
	"q=SELECT MEAN(temp), MAX(co) FROM home WHERE time > now() - 15m GROUP BY room" -G
http GET "/query?db=$DB" --data-urlencode "q=SHOW TAG VALUES FROM home WITH KEY = room" -G

step "6. last value cache (newest temp/hum/co per room) and distinct value cache (rooms)"
influxdb3 create last_cache --database "$DB" --table home --key-columns room \
	--value-columns temp,hum,co --count 1 --ttl 4h home_last >/dev/null
influxdb3 create distinct_cache --database "$DB" --table home --columns room home_rooms >/dev/null
sql "SELECT name, key_column_names, value_column_names, count, ttl FROM system.last_caches"
echo "the caches fill from writes after they are created; writing one new reading per room:"
now=$(date +%s)
http POST "/api/v3/write_lp?db=$DB&precision=second" --data-binary "home,room=Kitchen temp=22.9,hum=36.4,co=4i $now
home,room=Living\ Room temp=21.8,hum=35.7,co=0i $now
home,room=Office temp=23.1,hum=37.0,co=0i $now
home,room=Garage temp=15.6,hum=49.8,co=0i $now"
sql "SELECT * FROM last_cache('home', 'home_last') ORDER BY room"
sql "SELECT * FROM distinct_cache('home', 'home_rooms') ORDER BY room"

step "7. processing engine: a WAL trigger (plugins/temp_alert.py) and a schedule trigger (plugins/rollup.py)"
influxdb3 create trigger --database "$DB" --trigger-spec table:home --path temp_alert.py \
	--trigger-arguments max_temp=23 temp_alert
influxdb3 create trigger --database "$DB" --trigger-spec every:5s --path rollup.py \
	--trigger-arguments window=15m rollup
sql "SELECT trigger_name, plugin_filename, trigger_specification, disabled FROM system.processing_engine_triggers"
now=$(date +%s)
echo "writing two readings, one above max_temp=23:"
http POST "/api/v3/write_lp?db=$DB&precision=second" --data-binary "home,room=Kitchen temp=24.6,hum=36.0,co=6i $now
home,room=Office temp=22.8,hum=37.1,co=0i $now"
sleep 7
echo "home_alerts (written by temp_alert.py on WAL flush):"
sql "SELECT time, room, temp, max_temp FROM home_alerts ORDER BY time"
echo "home_rollup (written every 5 s by rollup.py), newest run:"
sql "SELECT time, room, readings, round(avg_temp, 2) AS avg_temp, max_temp FROM home_rollup
     WHERE time = (SELECT max(time) FROM home_rollup) ORDER BY room"
sql "SELECT event_time, trigger_name, log_text FROM system.processing_engine_logs
     WHERE log_text NOT LIKE '%execution%' AND event_time > now() - INTERVAL '8 seconds'
     ORDER BY event_time DESC LIMIT 4"

step "8. persistence: each snapshot writes the buffered rows to Parquet, one file per table per 10-minute chunk"
for _ in $(seq 30); do
	n=$(influxdb3 query --database "$DB" --format csv \
		"SELECT count(*) FROM system.parquet_files WHERE table_name = 'home'" | tail -1)
	[ "${n:-0}" -gt 0 ] && break
	sleep 2
done
sql "SELECT path, size_bytes, row_count, to_timestamp(min_time) AS min_time, to_timestamp(max_time) AS max_time
     FROM system.parquet_files WHERE table_name = 'home' ORDER BY path"
echo "a query reads the Parquet files plus any rows still only in the in-memory buffer:"
sql "SELECT count(*) AS rows_in_home FROM home"
