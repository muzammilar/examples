#!/bin/sh
# `make failover`: kill the observer that holds the `test` tenant's leaders, show that the
# tenant stays writable through the two surviving zones and that the leaders (and, if it was
# there, the root service) moved, then start the observer again and wait until it has rejoined
# and its replicas have caught up. FAILOVER_NODE=ob2|ob3 kills that observer instead.
set -eu
cd "$(dirname "$0")/.."

q() { # q CONTAINER USER SQL: run SQL on the observer in CONTAINER, table output
	docker exec -i "$1" obclient -h127.1 -P2881 -u"$2" -A --table -e "$3"
}
v() { # v CONTAINER USER SQL: single value, no headers
	docker exec -i "$1" obclient -h127.1 -P2881 -u"$2" -A -N -s -e "$3" 2>/dev/null | tr -d '\r'
}
now_ms() { python3 -c 'import time; print(int(time.time() * 1000))'; }

LOCATIONS="SELECT LS_ID, ZONE, SVR_IP, ROLE FROM oceanbase.DBA_OB_LS_LOCATIONS ORDER BY LS_ID, ZONE"
SERVERS="SELECT SVR_IP, ZONE, STATUS, WITH_ROOTSERVER FROM oceanbase.DBA_OB_SERVERS ORDER BY ZONE"
LOGSTAT="SELECT LS_ID, SVR_IP, ROLE, IN_SYNC FROM oceanbase.GV\$OB_LOG_STAT ORDER BY LS_ID, SVR_IP"
ip_of() { echo "172.28.10.1${1#ob}"; }

leader_ip=$(v ob1 root@test "SELECT SVR_IP FROM oceanbase.DBA_OB_LS_LOCATIONS WHERE LS_ID = 1001 AND ROLE = 'LEADER'")
victim=${FAILOVER_NODE:-ob${leader_ip##*.1}}
case $victim in ob1) survivor=ob2 ;; *) survivor=ob1 ;; esac

echo "==> before: user log stream 1001 leader on $leader_ip; killing $victim ($(ip_of "$victim"))"
q "$survivor" root@test "$LOCATIONS"
q "$survivor" root@sys "$SERVERS"
q "$survivor" root@test "CREATE DATABASE IF NOT EXISTS failover; CREATE TABLE IF NOT EXISTS failover.writes (
	id BIGINT AUTO_INCREMENT PRIMARY KEY, at TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6), phase VARCHAR(16));
	INSERT INTO failover.writes (phase) VALUES ('before')"

docker kill "$victim" >/dev/null
t0=$(now_ms)
echo "==> $victim killed (SIGKILL); retrying an INSERT through $survivor until it commits"
tries=0
until err=$(docker exec -i "$survivor" obclient -h127.1 -P2881 -uroot@test -A -e \
	"SET SESSION ob_query_timeout = 2000000; INSERT INTO failover.writes (phase) VALUES ('during')" 2>&1); do
	tries=$((tries + 1))
	[ $tries -gt 1 ] || echo "    attempt 1: $err"
	[ $tries -lt 120 ] || { echo "tenant test not writable after $tries tries: $err" >&2; exit 1; }
	sleep 0.2
done
echo "==> INSERT committed $(( $(now_ms) - t0 )) ms after the kill ($tries failed attempts), with 2 of 3 zones up"
# The killed server's replicas stay listed until the server is declared INACTIVE
# (server_permanent_offline_time is much longer); the LEADER role has moved.
for _ in $(seq 1 60); do
	new=$(v "$survivor" root@test "SELECT SVR_IP FROM oceanbase.DBA_OB_LS_LOCATIONS WHERE LS_ID = 1001 AND ROLE = 'LEADER'")
	[ -n "$new" ] && [ "$new" != "$(ip_of "$victim")" ] && break
	sleep 1
done
echo "==> during: leaders moved off $(ip_of "$victim")"
q "$survivor" root@test "$LOCATIONS"
q "$survivor" root@test "$LOGSTAT"
q "$survivor" root@test "INSERT INTO failover.writes (phase) VALUES ('during');
	SELECT phase, COUNT(*) AS writes FROM failover.writes GROUP BY phase ORDER BY MIN(id)"
# the server heartbeat lease is about 10 s; afterwards DBA_OB_SERVERS shows it INACTIVE
for _ in $(seq 1 60); do
	[ "$(v "$survivor" root@sys "SELECT STATUS FROM oceanbase.DBA_OB_SERVERS WHERE SVR_IP = '$(ip_of "$victim")'")" = INACTIVE ] && break
	sleep 1
done
q "$survivor" root@sys "$SERVERS"

echo "==> starting $victim again; waiting until it is ACTIVE and all replicas are in sync"
docker start "$victim" >/dev/null
t1=$(now_ms)
for _ in $(seq 1 300); do
	active=$(v "$survivor" root@sys "SELECT COUNT(*) FROM oceanbase.DBA_OB_SERVERS WHERE STATUS = 'ACTIVE' AND START_SERVICE_TIME IS NOT NULL")
	synced=$(v "$survivor" root@test "SELECT COUNT(*) FROM oceanbase.GV\$OB_LOG_STAT WHERE IN_SYNC = 'YES' OR ROLE = 'LEADER'")
	[ "$active" = 3 ] && [ "$synced" = 6 ] && break
	sleep 1
done
[ "$active" = 3 ] && [ "$synced" = 6 ] || { echo "$victim did not rejoin (active=$active, synced replicas=$synced)" >&2; exit 1; }
echo "==> $victim rejoined after $(( $(now_ms) - t1 )) ms; replicas in sync"
# With PRIMARY_ZONE 'zone1;zone2;zone3' the root service switches the leaders of `test` and
# its META$1002 tenant back to zone1 on its own once zone1 is healthy again (the sys tenant
# has PRIMARY_ZONE RANDOM, so its leader and the root service stay where they are). Wait for
# all three log streams, so a following `make benchmark` does not run into the switch
# (a leader switch rolls back the transactions open on the old leader: error 6002).
t2=$(now_ms)
for _ in $(seq 1 180); do
	back=$(v "$survivor" root@sys "SELECT COUNT(*) FROM oceanbase.CDB_OB_LS_LOCATIONS
		WHERE TENANT_ID IN (1001, 1002) AND ROLE = 'LEADER' AND ZONE = 'zone1'")
	[ "$back" = 3 ] && break
	sleep 1
done
echo "==> leaders of test and META\$1002 back in zone1: $back of 3 log streams, after $(( $(now_ms) - t2 )) ms"
q "$survivor" root@test "INSERT INTO failover.writes (phase) VALUES ('after'); $LOCATIONS; $LOGSTAT"
q "$survivor" root@sys "$SERVERS"
q "$survivor" root@test "SELECT phase, COUNT(*) AS writes FROM failover.writes GROUP BY phase ORDER BY MIN(id); DROP DATABASE failover"
