#!/bin/bash
# `make failover`: inserts one row per attempt (id = attempt number) through HAProxy :5000 for
# DURATION seconds, a fresh connection each time, and logs "<epoch ms> <id> ok|fail <error>".
# An id logged "ok" was acknowledged by a primary; failover.sh checks it survived.
set -u
DURATION=${DURATION:-60}
end=$(($(date +%s) + DURATION))
psql -qX -c 'CREATE TABLE IF NOT EXISTS failover_log (id bigint PRIMARY KEY, at timestamptz DEFAULT now())' -c 'TRUNCATE failover_log' >/dev/null
id=0
while [ "$(date +%s)" -lt "$end" ]; do
	id=$((id + 1))
	if err=$(psql -qXAt -c "INSERT INTO failover_log (id) VALUES ($id)" 2>&1); then
		echo "$(date +%s%3N) $id ok"
	else
		echo "$(date +%s%3N) $id fail $(echo "$err" | head -1)"
		sleep 0.2
	fi
done
