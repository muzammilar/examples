#!/bin/sh
# Manual failover on Memgraph Community replication, under write load:
#  1. client writes :Tick nodes to MAIN (4 workers, 45 s), logging every acknowledged seq
#  2. t=10 s: docker kill memgraph-main (SIGKILL)
#  3. compare each replica with the acknowledged writes (what was / was not replicated)
#  4. promote the SYNC replica (memgraph-replica-1) to MAIN, re-register replica-2 under it
#  5. start the old MAIN: it comes back as MAIN (a second MAIN) and accepts a write. Demoting
#     and registering it fails (it diverged), so its data is dropped and it rejoins empty
#  6. after the load: every instance against every acknowledged write
set -eu
cd "$(dirname "$0")/.."
q() { echo "$2" | docker exec -i "$1" mgconsole --output-format=csv 2>&1 | tail -n +2 | tr -d '"'; }
ts() { date +%s.%N | cut -c1-14; }
el() { awk "BEGIN { printf \"%.2f\", $2 - $1 }"; }
# REGISTER REPLICA on $1, retried: under write load it can fail with "Error: 6" (NO_ACCESS: the
# replication-state lock was busy, src/replication_handler/replication_handler.cpp) and with
# "Error: 3" (CONNECTION_FAILED) while the replica is still starting. Prints each distinct error once.
reg() {
	i=0 last= t0=$(ts)
	while :; do
		i=$((i + 1))
		out=$(q "$1" "$2")
		case $out in *Error*) ;; *) echo "registered after $i attempt(s), $(el $t0 $(ts)) s"; return 0 ;; esac
		[ "$out" != "$last" ] && echo "attempt $i: $out" && last=$out
		[ $i -ge "${3:-120}" ] && { echo "gave up after $i attempts ($(el $t0 $(ts)) s)"; return 1; }
		sleep 0.5
	done
}

[ "$(q memgraph-main 'SHOW REPLICATION ROLE;')" = main ] ||
	{ echo "memgraph-main is not MAIN (already failed over?): make down && make up"; exit 1; }
mkdir -p results
q memgraph-main 'MATCH (t:Tick) DETACH DELETE t;' >/dev/null

echo "== 1. write load: 4 workers for 45 s against memgraph-main (client retries on the next host)"
docker compose --profile tools run --rm --no-deps -e DURATION=45 -e WORKERS=4 client write \
	> results/failover-load.txt 2>&1 &
load=$!
sleep 10

echo "== 2. docker kill memgraph-main"
t_kill=$(ts)
docker kill --signal KILL memgraph-main >/dev/null

echo "== 3. replicas right after the kill (load still running, writes now fail)"
sleep 1
ALLOW=1
docker compose --profile tools run --rm --no-deps -e ALLOW_MISSING=1 \
	-e HOSTS=memgraph-replica-1:7687,memgraph-replica-2:7687 client verify
echo "SHOW REPLICAS on memgraph-replica-1 / replica role:"; q memgraph-replica-1 'SHOW REPLICATION ROLE;'

echo "== 4. promote memgraph-replica-1 (SYNC) to MAIN, register memgraph-replica-2 (ASYNC) under it"
t_p0=$(ts)
q memgraph-replica-1 'SET REPLICATION ROLE TO MAIN;'
reg memgraph-replica-1 "REGISTER REPLICA replica2 ASYNC TO 'memgraph-replica-2:10000';"
t_p1=$(ts)
echo "promotion took $(el $t_p0 $t_p1) s (two queries), $(el $t_kill $t_p1) s after the kill"
echo "SHOW REPLICAS;" | docker exec -i memgraph-replica-1 mgconsole --output-format=tabular

sleep 5
echo "== 5. start the old MAIN again"
docker start memgraph-main >/dev/null
until echo 'RETURN 1;' | docker exec -i memgraph-main mgconsole >/dev/null 2>&1; do sleep 0.5; done
echo "memgraph-main role after restart: $(q memgraph-main 'SHOW REPLICATION ROLE;') (memgraph-replica-1 is also: $(q memgraph-replica-1 'SHOW REPLICATION ROLE;'))"
echo "memgraph-main SHOW REPLICAS (restored from its own state):"
echo "SHOW REPLICAS;" | docker exec -i memgraph-main mgconsole --output-format=tabular || true
echo "a write on the old MAIN now is accepted, and only it has it:"
q memgraph-main "CREATE (:Stray {note: 'written on the old MAIN after failover'}) RETURN 'accepted';"
echo "demote it and register it as a SYNC replica of the new MAIN:"
q memgraph-main 'SET REPLICATION ROLE TO REPLICA WITH PORT 10000;'
reg memgraph-replica-1 "REGISTER REPLICA replica0 SYNC TO 'memgraph-main:10000';" 6 || true
echo ":Stray nodes  old main: $(q memgraph-main 'MATCH (s:Stray) RETURN count(s);')  new main: $(q memgraph-replica-1 'MATCH (s:Stray) RETURN count(s);')  replica-2: $(q memgraph-replica-2 'MATCH (s:Stray) RETURN count(s);')"
echo "drop the old MAIN's data and start it empty"
t_r0=$(ts)
docker compose rm --stop --force main >/dev/null 2>&1
docker volume rm memgraph-cluster_main-data >/dev/null
docker compose up --detach --wait main >/dev/null 2>&1
q memgraph-main 'SET REPLICATION ROLE TO REPLICA WITH PORT 10000;'
reg memgraph-replica-1 "REGISTER REPLICA replica0 SYNC TO 'memgraph-main:10000';"
until q memgraph-replica-1 'SHOW REPLICAS;' | grep '^replica0,' | grep -q 'status: ready'; do sleep 0.2; done
echo "load still running: $(kill -0 $load 2>/dev/null && echo yes || echo no)"
echo "old MAIN rejoined as SYNC replica in $(el $t_r0 $(ts)) s (wipe, start, register, initial sync)"
echo "SHOW REPLICAS;" | docker exec -i memgraph-replica-1 mgconsole --output-format=tabular

echo "== 6. load result"
wait $load || true
cat results/failover-load.txt
sleep 3
echo "== every instance against every acknowledged write"
docker compose --profile tools run --rm --no-deps -e HOSTS=memgraph-replica-1:7687,memgraph-replica-2:7687,memgraph-main:7687 client verify
