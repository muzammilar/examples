#!/bin/bash
# Scale the cluster out and back in while sysbench keeps writing to tenant `test`.
#   scripts/scale.sh out    start ob4..ob6, ALTER SYSTEM ADD SERVER (one per zone),
#                           ALTER RESOURCE TENANT test UNIT_NUM = 2, wait for the balance job
#   scripts/scale.sh in     ALTER RESOURCE TENANT test UNIT_NUM = 1 DELETE UNIT_GROUP (<new group>),
#                           wait for the units to go, ALTER SYSTEM DELETE SERVER, stop ob4..ob6
#   scripts/scale.sh demo   load on; 60 s baseline; out; 60 s; in; 60 s; load off; summary
#   scripts/scale.sh status servers, units, log streams and leaders, tablets per log stream
# Run from the example directory (the Makefile does). Times are printed relative to the
# start of the command; `demo` also writes results/scale-<stamp>.{log,json}.
set -euo pipefail
cd "$(dirname "$0")/.."

NEW=(ob4 ob5 ob6)
NEW_IPS=(172.28.12.14 172.28.12.15 172.28.12.16)
ZONES=(zone1 zone2 zone3)
LOAD_THREADS=${LOAD_THREADS:-16}
LOAD_TABLES=${LOAD_TABLES:-4}
LOAD_SIZE=${LOAD_SIZE:-50000}
LOAD_WORKLOAD=${LOAD_WORKLOAD:-oltp_read_write}
STEADY=${STEADY:-60}
# obproxy routes every statement to the partition's leader; LOAD_HOST=ob1 connects to one
# observer directly, which forwards whatever it does not lead itself
LOAD_HOST=${LOAD_HOST:-obproxy}
case $LOAD_HOST in obproxy) LOAD_PORT=2883 LOAD_USER='root@test#obcluster' ;; *) LOAD_PORT=2881 LOAD_USER=root@test ;; esac

sys() { docker exec -i ob1 obclient -h127.1 -P2881 -uroot@sys -A --table -e "$1"; }
sysv() { docker exec -i ob1 obclient -h127.1 -P2881 -uroot@sys -A -N -s -e "$1" 2>/dev/null | tr -d '\r'; }
tst() { docker exec -i ob1 obclient -h127.1 -P2881 -uroot@test -A --table -e "$1"; }
now() { python3 -c 'import time; print(time.time())'; }
T0=${T0:-$(now)}
ts() { python3 -c "print('%6.1f' % ($(now) - $T0))"; }
say() { echo "[$(ts)s] $*"; }

UNITS="SELECT u.UNIT_ID, u.UNIT_GROUP_ID, u.ZONE, u.SVR_IP, u.STATUS, u.MAX_CPU,
  ROUND(u.MEMORY_SIZE/1073741824, 1) AS mem_gb
  FROM oceanbase.DBA_OB_UNITS u WHERE u.TENANT_ID = 1002 ORDER BY u.ZONE, u.UNIT_ID"
LS="SELECT l.LS_ID, l.STATUS, l.UNIT_LIST,
  (SELECT SVR_IP FROM oceanbase.DBA_OB_LS_LOCATIONS x WHERE x.LS_ID = l.LS_ID AND x.ROLE = 'LEADER') AS leader,
  (SELECT COUNT(*) FROM oceanbase.DBA_OB_TABLET_TO_LS t WHERE t.LS_ID = l.LS_ID AND t.TABLET_ID >= 200001) AS user_tablets
  FROM oceanbase.DBA_OB_LS l ORDER BY l.LS_ID"
SERVERS="SELECT SVR_IP, ZONE, STATUS, WITH_ROOTSERVER FROM oceanbase.DBA_OB_SERVERS ORDER BY ZONE, SVR_IP"
TABLES="SELECT TABLE_NAME, COUNT(DISTINCT TABLET_ID) AS tablets, LS_ID, SVR_IP AS leader
  FROM oceanbase.DBA_OB_TABLE_LOCATIONS
  WHERE DATABASE_NAME IN ('sbtest', 'demo') AND ROLE = 'LEADER' AND TABLE_TYPE = 'USER TABLE'
  GROUP BY TABLE_NAME, LS_ID, SVR_IP ORDER BY TABLE_NAME, LS_ID"

status() {
	sys "$SERVERS; $UNITS"
	tst "$LS; $TABLES"
}

# wait_until DESCRIPTION SECONDS SQL EXPECTED: poll a sys-tenant query once a second
wait_until() {
	local what=$1 secs=$2 sql=$3 want=$4 got=""
	for _ in $(seq 1 "$secs"); do
		got=$(sysv "$sql")
		[ "$got" = "$want" ] && return 0
		sleep 1
	done
	echo "scale.sh: timed out waiting for $what (got '$got', want '$want')" >&2
	return 1
}

balance_watch() { # print every change of units / log streams until no balance job is left
	local last="" cur
	for _ in $(seq 1 600); do
		cur=$(sysv "SELECT CONCAT('units ', GROUP_CONCAT(CONCAT(UNIT_ID, ':', STATUS) ORDER BY UNIT_ID)) FROM oceanbase.DBA_OB_UNITS WHERE TENANT_ID = 1002")
		cur="$cur | $(docker exec -i ob1 obclient -h127.1 -P2881 -uroot@test -A -N -s -e \
			"SELECT CONCAT('ls ', GROUP_CONCAT(CONCAT(LS_ID, '[', IFNULL(UNIT_LIST, ''), ']', STATUS) ORDER BY LS_ID SEPARATOR ' ')) FROM oceanbase.DBA_OB_LS" 2>/dev/null | tr -d '\r')"
		cur="$cur | $(sysv "SELECT CONCAT('jobs ', IFNULL(GROUP_CONCAT(CONCAT(JOB_TYPE, ':', STATUS)), '-'), ' tasks ',
			IFNULL((SELECT GROUP_CONCAT(CONCAT(TASK_TYPE, ' ', SRC_LS, '->', DEST_LS, ':', STATUS)) FROM oceanbase.CDB_OB_BALANCE_TASKS WHERE TENANT_ID = 1002), '-'))
			FROM oceanbase.CDB_OB_BALANCE_JOBS WHERE TENANT_ID = 1002")"
		[ "$cur" != "$last" ] && say "$cur"
		last=$cur
		if ! echo "$cur" | grep -q 'ADDING\|DELETING\|jobs [A-Z]\|CREATING\|DROPPING\|tasks [A-Z]'; then
			return 0
		fi
		sleep 1
	done
	echo "scale.sh: balance did not finish" >&2
	return 1
}

show_history() { # balance jobs, balance tasks and transfers since $1 (sys tenant time)
	sys "SELECT JOB_ID, JOB_TYPE, BALANCE_STRATEGY, ZONE_UNIT_NUM_LIST, STATUS,
	       TIMESTAMPDIFF(MICROSECOND, CREATE_TIME, FINISH_TIME) DIV 1000 AS ms
	  FROM oceanbase.CDB_OB_BALANCE_JOB_HISTORY WHERE TENANT_ID = 1002 AND CREATE_TIME >= '$1' ORDER BY JOB_ID;
	SELECT TASK_ID, TASK_TYPE, SRC_LS, DEST_LS, FINISHED_PART_COUNT AS parts, STATUS,
	       TIMESTAMPDIFF(MICROSECOND, CREATE_TIME, FINISH_TIME) DIV 1000 AS ms
	  FROM oceanbase.CDB_OB_BALANCE_TASK_HISTORY WHERE TENANT_ID = 1002 AND CREATE_TIME >= '$1' ORDER BY TASK_ID;
	SELECT TASK_ID, SRC_LS, DEST_LS, PART_COUNT AS parts, STATUS,
	       TIMESTAMPDIFF(MICROSECOND, CREATE_TIME, FINISH_TIME) DIV 1000 AS ms
	  FROM oceanbase.CDB_OB_TRANSFER_TASK_HISTORY WHERE TENANT_ID = 1002 AND CREATE_TIME >= '$1' ORDER BY TASK_ID"
}

scale_out() {
	local since
	since=$(sysv "SELECT NOW(6)")
	say "scale out: starting ${NEW[*]} (second observer in each zone)"
	if ! docker compose --profile scale up --detach --wait --wait-timeout 300 "${NEW[@]}" >/tmp/scale-up.$$ 2>&1; then
		grep -v '^ *Container' /tmp/scale-up.$$ || true
		rm -f /tmp/scale-up.$$
		for c in "${NEW[@]}"; do # an observer that cannot preallocate its data file / log disk exits 240
			docker cp "$c:/root/ob/log/observer.log" - 2>/dev/null | tar -xO 2>/dev/null |
				grep -m1 -E 'ERROR.*(not enough|NOT_ENOUGH)' | sed "s/^/$c: /" | cut -c1-300 || true
		done
		echo "scale.sh: ${NEW[*]} did not all start (free disk in the Docker VM? see Known issues)" >&2
		exit 1
	fi
	rm -f /tmp/scale-up.$$
	say "observers listening; ALTER SYSTEM ADD SERVER"
	for i in 0 1 2; do
		sys "ALTER SYSTEM ADD SERVER '${NEW_IPS[$i]}:2882' ZONE '${ZONES[$i]}'"
	done
	wait_until "6 ACTIVE servers" 180 \
		"SELECT COUNT(*) FROM oceanbase.DBA_OB_SERVERS WHERE STATUS = 'ACTIVE' AND START_SERVICE_TIME IS NOT NULL" 6
	say "6 servers ACTIVE"
	sys "$SERVERS"
	say "ALTER RESOURCE TENANT test UNIT_NUM = 2"
	sys "ALTER RESOURCE TENANT test UNIT_NUM = 2"
	balance_watch
	say "scale out done"
	show_history "$since"
	status
}

scale_in() {
	local since group
	since=$(sysv "SELECT NOW(6)")
	# the unit group on the servers being removed; without DELETE UNIT_GROUP the root
	# service picks the group to drop itself (here it picked the original one, on ob1..ob3)
	group=$(sysv "SELECT DISTINCT UNIT_GROUP_ID FROM oceanbase.DBA_OB_UNITS WHERE TENANT_ID = 1002 AND SVR_IP IN ('${NEW_IPS[0]}', '${NEW_IPS[1]}', '${NEW_IPS[2]}')")
	if [ -n "$group" ]; then
		say "ALTER RESOURCE TENANT test UNIT_NUM = 1 DELETE UNIT_GROUP ($group)"
		sys "ALTER RESOURCE TENANT test UNIT_NUM = 1 DELETE UNIT_GROUP ($group)"
		balance_watch
	fi
	say "ALTER SYSTEM DELETE SERVER ${NEW_IPS[*]}"
	sys "ALTER SYSTEM DELETE SERVER '${NEW_IPS[0]}:2882', '${NEW_IPS[1]}:2882', '${NEW_IPS[2]}:2882'"
	wait_until "servers removed" 600 "SELECT COUNT(*) FROM oceanbase.DBA_OB_SERVERS" 3
	# obproxy refreshes its server list on its own schedule; stopping the containers right
	# away left it sending connections to the removed servers for ~40 s (connect errors,
	# retries, p95 up to 5 s). Give it DRAIN seconds first.
	say "servers deleted; waiting ${DRAIN:-30} s for obproxy to drop them, then stopping ${NEW[*]}"
	sleep "${DRAIN:-30}"
	docker compose --profile scale rm --stop --force "${NEW[@]}" >/dev/null 2>&1
	say "scale in done"
	show_history "$since"
	status
}

demo() {
	local stamp log json t_out t_out_done t_in t_in_done t_end
	stamp=$(date -u +%Y%m%dT%H%M%SZ)
	mkdir -p results
	log=results/scale-$stamp.log
	json=results/scale-$stamp.json
	SB="docker compose run --rm --no-deps -T sysbench"
	common="--db-driver=mysql --mysql-host=$LOAD_HOST --mysql-port=$LOAD_PORT --mysql-user=$LOAD_USER --mysql-password=
		--mysql-db=sbtest --tables=$LOAD_TABLES --table-size=$LOAD_SIZE"
	docker compose build --quiet sysbench
	docker exec -i ob1 obclient -h127.1 -P2881 -uroot@test -A -e 'DROP DATABASE IF EXISTS sbtest; CREATE DATABASE sbtest'
	echo "prepare: $LOAD_TABLES tables x $LOAD_SIZE rows"
	$SB oltp_common $common --threads="$LOAD_TABLES" prepare >"$log.prepare" 2>&1 || { tail "$log.prepare"; exit 1; }
	T0=$(now)
	say "load: $LOAD_WORKLOAD, $LOAD_THREADS threads through $LOAD_HOST, report every 5 s -> $log"
	# --mysql-ignore-errors=all: count failed statements (shown as err/s) instead of stopping
	$SB "$LOAD_WORKLOAD" $common --threads="$LOAD_THREADS" --time=0 --report-interval=5 \
		--mysql-ignore-errors=all run >"$log" 2>&1 &
	load_pid=$!
	trap 'docker ps -q --filter "label=com.docker.compose.oneoff=True" --filter "label=com.docker.compose.service=sysbench" | xargs -r docker kill >/dev/null 2>&1 || true' EXIT
	sleep "$STEADY"
	t_out=$(ts); scale_out; t_out_done=$(ts)
	sleep "$STEADY"
	t_in=$(ts); scale_in; t_in_done=$(ts)
	sleep "$STEADY"
	t_end=$(ts)
	docker ps -q --filter "label=com.docker.compose.oneoff=True" --filter "label=com.docker.compose.service=sysbench" | xargs -r docker kill >/dev/null
	wait "$load_pid" 2>/dev/null || true
	$SB oltp_common $common cleanup >/dev/null 2>&1 || true
	docker exec -i ob1 obclient -h127.1 -P2881 -uroot@test -A -e 'DROP DATABASE IF EXISTS sbtest'

	# average tps / qps / p95 / err per phase from the 5 s reports
	summary=$(awk -v a="$t_out" -v b="$t_out_done" -v c="$t_in" -v d="$t_in_done" -v e="$t_end" '
		/^\[ *[0-9]+s \]/ {
			t = $2; sub(/s/, "", t); t += 0
			for (i = 1; i <= NF; i++) {
				if ($i == "tps:") tps = $(i + 1); if ($i == "qps:") qps = $(i + 1)
				if ($i == "err/s:") err = $(i + 1); if ($i == "(ms,95%):") p95 = $(i + 1)
			}
			p = t <= a ? "1 before (3 servers, UNIT_NUM 1)" : t <= b ? "2 scaling out" : t <= c ? "3 after scale-out (6 servers, UNIT_NUM 2)" : \
			    t <= d ? "4 scaling in" : t <= e ? "5 after scale-in (3 servers, UNIT_NUM 1)" : ""
			if (p == "") next
			n[p]++; s[p] += tps; q[p] += qps; r[p] += err; if (p95 > m[p]) m[p] = p95; P[p] = 1
			if (min[p] == "" || tps < min[p]) min[p] = tps
		}
		END {
			for (p in P) printf "%-44s %4d %9.1f %9.1f %9.1f %9.2f %9.2f\n", substr(p, 3), n[p] * 5, s[p] / n[p], min[p], q[p] / n[p], m[p], r[p] / n[p]
		}' "$log" | sort -k1,1)
	echo
	printf '%-44s %4s %9s %9s %9s %9s %9s\n' phase secs "avg tps" "min tps" "avg qps" "max p95" "err/s"
	# re-sort by phase order
	for p in "before" "scaling out" "after scale-out" "scaling in" "after scale-in"; do
		echo "$summary" | grep "^$p" || true
	done
	echo "phases (s since load start): out $t_out-$t_out_done, in $t_in-$t_in_done, end $t_end"
	cat >"$json" <<JSON
{"timestamp": "$stamp", "system": "oceanbase-scale-out-in", "workload": "$LOAD_WORKLOAD", "host": "$LOAD_HOST",
 "threads": $LOAD_THREADS, "tables": $LOAD_TABLES, "table_size": $LOAD_SIZE,
 "phases_s": {"out": [$t_out, $t_out_done], "in": [$t_in, $t_in_done], "end": $t_end},
 "docker": {"cpus": $(docker info --format '{{.NCPU}}'), "mem_bytes": $(docker info --format '{{.MemTotal}}')},
 "raw": "$log"}
JSON
	echo "raw: $log  json: $json"
}

case ${1:-} in
out) scale_out ;;
in) scale_in ;;
demo) demo ;;
status) status ;;
*) echo "usage: $0 out|in|demo|status" >&2; exit 2 ;;
esac
