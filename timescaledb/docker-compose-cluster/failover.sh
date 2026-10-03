#!/bin/bash
# `make failover`: stop the primary under a steady stream of inserts, watch Patroni promote a
# replica, start the old primary again (it rejoins as a replica via pg_rewind), then check that
# every acknowledged insert is on the new primary.
set -euo pipefail
DURATION=${DURATION:-60}
STOP_AFTER=${STOP_AFTER:-10}
PATRONICTL=(patronictl -c /config/patroni.yml)
PSQL=(docker compose --progress quiet run --rm --no-TTY psql)
LOG=results/failover-$(date -u +%Y%m%dT%H%M%SZ).log
mkdir -p results

leader() {
	for n in tsdb1 tsdb2 tsdb3; do
		[ "$(docker exec "$n" curl -s -o /dev/null -w '%{http_code}' localhost:8008/primary 2>/dev/null)" = 200 ] && { echo "$n"; return; }
	done
	echo none
}
list() { for n in tsdb1 tsdb2 tsdb3; do docker exec "$n" "${PATRONICTL[@]}" list 2>/dev/null && return; done; }
sec() { awk "BEGIN { printf \"%.1f\", $1 / 1000 }"; }
ms() { python3 -c 'import time; print(int(time.time() * 1000))'; }

old=$(leader)
echo "==> cluster before (primary: $old)"
list
echo "==> $DURATION s of inserts through HAProxy :5000 (one connection per insert), log: $LOG"
docker compose --progress quiet run --rm --no-TTY -e DURATION="$DURATION" --entrypoint bash psql /bench/writer.sh >"$LOG" 2>&1 &
writer=$!
sleep "$STOP_AFTER"
t0=$(ms)
echo "==> $(date -u +%T) docker stop $old (the primary)"
docker stop "$old" >/dev/null
until [ "$(leader)" != none ] && [ "$(leader)" != "$old" ]; do sleep 0.5; done
t1=$(ms)
new=$(leader)
echo "==> $(date -u +%T) $new is the new primary, $(sec $((t1 - t0))) s after the stop"
list
sleep 10
echo "==> $(date -u +%T) docker start $old"
docker start "$old" >/dev/null
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$old")" = healthy ]; do sleep 1; done
echo "==> $(date -u +%T) $old is back"
wait "$writer"
list

ok=$(grep -c ' ok$' "$LOG" || true)
fail=$(grep -c ' fail ' "$LOG" || true)
first_fail=$(awk '$3 == "fail" { print $1; exit }' "$LOG")
last_fail=$(awk '$3 == "fail" { t = $1 } END { print t }' "$LOG")
echo "==> writer: $ok inserts acknowledged, $fail attempts failed"
if [ -n "$first_fail" ]; then
	echo "    writes failed for $(sec $((last_fail - first_fail))) s (first to last failed attempt)"
	awk '$3 == "fail" { $1 = $2 = $3 = ""; print }' "$LOG" | sed 's/^ *//' | sort | uniq -c | sort -rn | head -5
fi
# every acknowledged id must be on the new primary
awk '$3 == "ok" { print $2 }' "$LOG" | sort >results/.acked
"${PSQL[@]}" -XAtc 'SELECT id FROM failover_log' | sort >results/.present
lost=$(comm -23 results/.acked results/.present | wc -l | tr -d ' ')
echo "==> rows on the new primary: $(wc -l <results/.present | tr -d ' '); acknowledged but missing: $lost"
if [ "$lost" -gt 0 ]; then
	echo "    with asynchronous replication the old primary acknowledged them before a replica had"
	echo "    them; \`make sync\` turns on synchronous_mode, which waits for one replica"
fi
rm -f results/.acked results/.present
"${PSQL[@]}" -c "SELECT pg_is_in_recovery() AS in_recovery, timeline_id FROM pg_control_checkpoint()"
