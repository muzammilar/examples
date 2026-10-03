#!/usr/bin/env bash
# Cluster operations for the Makefile targets, each while client/load.py commits transfers:
#   ops.sh scale-out        3 actives -> 3 actives + 2 standbys
#   ops.sh scale-in         back to 3 actives
#   ops.sh standby          stop 2 of 3 actives: the standbys do not keep the cluster available
#   ops.sh replace R        lose replica R's data file, `tigerbeetle recover` it
#   ops.sh rolling-restart  recreate every replica one at a time (picks up CACHE_GRID etc.)
set -euo pipefail

ACTIVES="replica-0 replica-1 replica-2"
A3=10.203.54.10:3000,10.203.54.11:3000,10.203.54.12:3000
A5=$A3,10.203.54.13:3000,10.203.54.14:3000
DURATION=${DURATION:-40}

logs() { docker logs "$(docker compose ps -q "$1")" 2>&1; }
# the primary of view v is replica v mod 3; the highest view any active has logged entering is current
primary() {
	for s in $ACTIVES; do logs $s; done | grep -o 'transition_to_normal[a-z_]*: view=[0-9.]*' |
		sed 's/.*[=.]//' | sort -n | tail -1 | awk '{print $1 % 3}'
}
running_standbys() { docker compose --profile standby ps --services --status running | grep standby || true; }
now() { date -u +%T; }

# client/load.py in the background for DURATION s
load_start() {
	mkdir -p results
	load_out=results/load-$1-$(date -u +%Y%m%dT%H%M%SZ).txt
	docker compose --progress quiet build -q client >/dev/null
	DURATION=$DURATION docker compose --progress quiet run --rm -T client python -u /load.py >"$load_out" 2>&1 &
	load_pid=$!
	until grep -q '^load:' "$load_out" 2>/dev/null; do sleep 0.5; done
	sleep 5
}
load_wait() {
	local rc=0
	wait $load_pid || rc=$?
	echo "==> client/load.py ($load_out):"
	grep -vE '^  t=' "$load_out"
	med=$(awk '/^  t=/ { print $(NF - 1) }' "$load_out" | sort -n | awk '{ a[NR] = $1 } END { print a[int((NR + 1) / 2)] }')
	echo "    median $med transfers/s; seconds below half of that:"
	awk -v med="$med" '/^  t=/ && $(NF - 1) < med / 2 { print "     " $0 }' "$load_out"
	return $rc
}

# recreate $1 (picking up the current environment) and wait until it is back in a normal view
recreate() {
	local t0
	t0=$(date +%s)
	echo "==> $(now) recreating $1"
	docker compose --profile standby up --detach --wait --no-deps "$1" 2>&1 | grep -vE 'Running|Waiting|Healthy' || true
	until logs "$1" | grep 'transition_to_normal' >/dev/null; do sleep 0.5; done
	echo "    $1 in view again after $(($(date +%s) - t0)) s: $(logs "$1" | grep -o 'transition_to_normal.*' | tail -n 1)"
}
# every active and running standby, one at a time: backups and standbys first, the primary last
roll() {
	local p
	p=$(primary)
	for s in $(echo $ACTIVES | tr ' ' '\n' | grep -vx replica-$p) $(running_standbys) replica-$p; do recreate $s; done
}

case $1 in
scale-out)
	load_start scale-out
	echo "==> $(now) actives get the 5-address list (2 standby slots), one at a time"
	export ADDRESSES=$A5
	roll
	for s in standby-3 standby-4; do
		echo "==> $(now) starting $s (formats with --standby)"
		docker compose --profile standby up --detach --wait --no-deps $s 2>&1 | grep -vE 'Running|Waiting|Healthy' || true
	done
	until [ "$(for s in standby-3 standby-4; do logs $s | grep -c transition_to_normal; done | grep -c '^[1-9]')" = 2 ]; do sleep 0.5; done
	echo "==> $(now) both standbys in view:"
	for s in standby-3 standby-4; do echo "    $s: $(logs $s | grep -o 'transition_to_normal.*' | tail -n 1)"; done
	load_wait
	;;
scale-in)
	load_start scale-in
	echo "==> $(now) removing standby-3, standby-4 and their volumes"
	docker compose --profile standby rm --stop --force standby-3 standby-4 2>&1 | grep -v Container || true
	docker volume rm tigerbeetle-ops_standby-3 tigerbeetle-ops_standby-4 >/dev/null
	echo "==> $(now) actives get the 3-address list again, one at a time"
	export ADDRESSES=$A3
	roll
	load_wait
	;;
standby)
	export ADDRESSES=$A5
	b1=replica-$((($(primary) + 1) % 3)) b2=replica-$((($(primary) + 2) % 3))
	echo "==> stopping two actives ($b1, $b2); the primary and both standbys stay up"
	docker compose stop $b1 $b2 2>&1 | grep -v Container || true
	echo "==> transfer 601 via the repl: 1 active + 2 standbys is no quorum (20 s timeout)"
	if docker compose exec -T replica-$(primary) timeout 20 /tigerbeetle repl --cluster=0 --addresses=$A3 \
		--command='create_accounts id=601 ledger=1 code=1;' 2>&1 | grep -v 'info(message_bus)'; then
		echo "    unexpected: the cluster answered"
	else echo "    no reply: the cluster is unavailable although both standbys are up"; fi
	echo "==> starting $b1: 2 of 3 actives are a quorum again"
	docker compose up --detach --wait --no-deps $b1 2>&1 | grep -vE 'Running|Waiting|Healthy|Container' || true
	t0=$(date +%s)
	docker compose exec -T $b1 timeout 60 /tigerbeetle repl --cluster=0 --addresses=$A3 \
		--command='create_accounts id=601 ledger=1 code=1;' 2>&1 | grep -v 'info(message_bus)' || true
	echo "    committed $(($(date +%s) - t0)) s after $b1 started"
	docker compose up --detach --wait --no-deps $b2 2>&1 | grep -vE 'Running|Waiting|Healthy|Container' || true
	;;
replace)
	r=${2:-$((($(primary) + 1) % 3))}
	[ -n "$(running_standbys)" ] && export ADDRESSES=$A5
	load_start replace
	echo "==> $(now) replica-$r: stop, remove container and volume tigerbeetle-ops_replica-$r"
	docker compose rm --stop --force replica-$r >/dev/null 2>&1
	docker volume rm tigerbeetle-ops_replica-$r >/dev/null
	t0=$(date +%s)
	RECOVER=1 docker compose up --detach --wait --no-deps replica-$r 2>&1 | grep -vE 'Running|Waiting|Healthy' || true
	until logs replica-$r | grep 'transition_to_normal' >/dev/null; do sleep 0.5; done
	echo "==> $(now) replica-$r back in view after $(($(date +%s) - t0)) s:"
	logs replica-$r | grep -E 'recover|sync|transition_to_normal' | grep -v debug | head -n 8
	load_wait
	;;
rolling-restart)
	[ -n "$(running_standbys)" ] && export ADDRESSES=$A5
	load_start rolling-restart
	roll
	load_wait
	;;
*) echo "usage: $0 scale-out|scale-in|standby|replace [R]|rolling-restart" >&2; exit 2 ;;
esac
