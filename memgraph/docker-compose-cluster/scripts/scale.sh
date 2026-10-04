#!/bin/sh
# Read replicas: add or remove memgraph-replica-3 and -4 (ASYNC) on the running MAIN.
#   sh scripts/scale.sh out   # start, set role, register, wait until each is ready
#   sh scripts/scale.sh in    # DROP REPLICA on MAIN, stop and remove the containers
#   sh scripts/scale.sh demo  # read load for 180 s: 2 replicas, scale-out at 20 s, scale-in 40 s
#                             # after the scale-out finished
set -eu
cd "$(dirname "$0")/.."
q() { echo "$2" | docker exec -i "$1" mgconsole --output-format=csv 2>&1 | tail -n +2 | tr -d '"'; }
ts() { date +%s.%N | cut -c1-14; }
el() { awk "BEGIN { printf \"%.1f\", $2 - $1 }"; }
NEW="replica3 replica4"
# REGISTER / DROP REPLICA on MAIN, retried every 0.5 s: under load both fail intermittently
# ("Couldn't register replica ... Error: 6" = NO_ACCESS, "Failed to unregister replica due to lack
# of unique access over the cluster state"). Prints each distinct error once.
reg() {
	i=0 last=
	while :; do
		i=$((i + 1))
		out=$(q memgraph-main "$1")
		case $out in *exception*) ;; *) echo "$1 -> ok after $i attempt(s)"; return 0 ;; esac
		[ "$out" != "$last" ] && echo "attempt $i: $out" && last=$out
		[ $i -ge 120 ] && { echo "gave up after $i attempts"; return 1; }
		sleep 0.5
	done
}

scale_out() {
	t0=$(ts)
	docker compose --profile scale up --detach --wait $NEW
	t1=$(ts)
	echo "containers healthy in $(el $t0 $t1) s"
	for s in $NEW; do
		n=${s#replica}
		q memgraph-replica-$n 'SET REPLICATION ROLE TO REPLICA WITH PORT 10000;' >/dev/null
		reg "REGISTER REPLICA $s ASYNC TO 'memgraph-replica-$n:10000';"
	done
	# caught up = the new replica has at least the :KNOWS edges MAIN had when it was registered
	# (SHOW REPLICAS status is no help: under writes an ASYNC replica flips between ready,
	# replicating, recovery and invalid)
	want=$(q memgraph-main 'MATCH ()-[k:KNOWS]->() RETURN count(k);')
	for s in $NEW; do
		n=${s#replica} i=0
		until [ "$(q memgraph-replica-$n 'MATCH ()-[k:KNOWS]->() RETURN count(k);')" -ge "$want" ] 2>/dev/null; do
			i=$((i + 1)); [ $i -lt 600 ] || { echo "$s not caught up after 120 s"; exit 1; }; sleep 0.2
		done
	done
	t2=$(ts)
	echo "registered and caught up ($want :KNOWS) in $(el $t1 $t2) s (scale-out total $(el $t0 $t2) s)"
	for s in $NEW; do n=${s#replica}; echo "memgraph-replica-$n: $(q memgraph-replica-$n 'MATCH (p:Person) RETURN count(p);') :Person"; done
}

scale_in() {
	t0=$(ts)
	for s in $NEW; do reg "DROP REPLICA $s;"; done
	sleep 1 # let clients see the new SHOW REPLICAS before the containers go away
	docker compose --profile scale rm --stop --force $NEW >/dev/null 2>&1
	for s in $NEW; do docker volume rm "memgraph-cluster_${s}-data" >/dev/null; done
	echo "scale-in took $(el $t0 $(ts)) s"
}

case ${1:-} in
out) scale_out ;;
in) scale_in ;;
demo)
	docker compose --profile tools run --rm --no-deps -e DURATION=180 client read > results/scale-demo.txt 2>&1 &
	load=$!
	# wait for the seed to finish and 20 s of load on 2 replicas
	until grep -q '^t= 20s' results/scale-demo.txt 2>/dev/null; do sleep 1; done
	echo "== scale-out: 2 -> 4 replicas (3 -> 5 instances)"; scale_out
	sleep 40
	echo "== scale-in: 4 -> 2 replicas"; scale_in
	wait $load
	cat results/scale-demo.txt
	;;
*) echo "usage: $0 out|in|demo" >&2; exit 2 ;;
esac
