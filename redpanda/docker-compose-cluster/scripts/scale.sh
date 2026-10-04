#!/bin/bash
# scale.sh out: start redpanda-3 and redpanda-4, wait until the partition balancer has moved
#               replicas onto them and no move is in progress.
# scale.sh in:  `rpk cluster brokers decommission` both, wait until their replicas have moved
#               off and they left the cluster, then remove the containers and their volumes.
# Prints replicas per broker for topic $TOPIC every 5 s.
set -euo pipefail
TOPIC=${TOPIC:-scale}
RPK="docker exec redpanda-cc-0 rpk"
log() { echo "[$(date -u +%T)] $*"; }
t0=$(date +%s)
el() { echo $(( $(date +%s) - t0 )); }
id_of() { $RPK cluster info -b | awk -v h="$1" '$1 ~ /^[0-9]/ && $2 == h { print $1 + 0 }'; }
spread() { # "id:replicas ..." for the topic, from the REPLICAS column
	$RPK topic describe "$TOPIC" --print-partitions | grep -o '\[[0-9 ]*\]' | tr -d '[]' | tr ' ' '\n' |
		sort -n | uniq -c | awk '{ printf "%s:%s ", $2, $1 }'
}
moving() { $RPK cluster partitions move-status 2>/dev/null | grep -c "^kafka/$TOPIC\|^$TOPIC " || true; }

case ${1:-} in
out)
	# Community behaviour, also after the 30-day trial: rebalance only when a broker is added.
	# (During the trial the default is the Enterprise `continuous` mode.)
	$RPK cluster config set partition_autobalancing_mode node_add >/dev/null
	# The balancer never places a replica on a broker whose disk is over this (default 80%). All
	# brokers share the Docker VM's disk, which is often fuller than that on a laptop; with the
	# default the new brokers get nothing ("No nodes are available to perform allocation after
	# hard constraints were solved" in the controller log).
	$RPK cluster config set partition_autobalancing_max_disk_usage_percent "${MAX_DISK_PCT:-99}" >/dev/null
	log "scale out: replicas per broker id before: $(spread)"
	docker compose --profile scale up --detach --wait redpanda-3 redpanda-4
	n3=$(id_of redpanda-3); n4=$(id_of redpanda-4)
	log "redpanda-3 joined as broker $n3, redpanda-4 as broker $n4 ($(el) s)"
	for i in $(seq 240); do
		sleep 5
		st=$($RPK cluster partitions balancer-status | awk '/^Status:/ { print $2 }')
		mv=$($RPK cluster partitions move-status 2>/dev/null | grep -c reconfiguration || true)
		sp=$(spread)
		log "balancer $st, $(el) s: replicas per broker id: $sp"
		echo "$sp" | grep -q " $n3:\|^$n3:" && echo "$sp" | grep -q " $n4:\|^$n4:" &&
			[ "$st" = ready ] && [ "$mv" = 0 ] && break
	done
	log "scale out done after $(el) s: $($RPK cluster info -b | awk '$1 ~ /^[0-9]/' | wc -l) brokers"
	;;
in)
	n3=$(id_of redpanda-3); n4=$(id_of redpanda-4)
	[ -n "$n3" ] && [ -n "$n4" ] || { echo "redpanda-3/4 are not in the cluster"; exit 1; }
	log "scale in: decommission brokers $n3 (redpanda-3) and $n4 (redpanda-4); replicas per broker id: $(spread)"
	$RPK cluster brokers decommission "$n3"
	$RPK cluster brokers decommission "$n4"
	for i in $(seq 240); do
		sleep 5
		left=$($RPK cluster info -b | awk -v a="$n3" -v b="$n4" '$1 ~ /^[0-9]/ && ($1 + 0 == a || $1 + 0 == b)' | wc -l)
		log "$(el) s: $left of 2 still members; replicas per broker id: $(spread)"
		[ "$left" = 0 ] && break
	done
	$RPK cluster brokers decommission-status "$n3" 2>&1 | head -3 || true
	log "scale in done after $(el) s; removing redpanda-3 and redpanda-4"
	docker compose --profile scale rm --stop --force -v redpanda-3 redpanda-4
	docker volume rm redpanda-cluster_redpanda-3 redpanda-cluster_redpanda-4 >/dev/null
	$RPK cluster info -b
	;;
*) echo "usage: $0 out|in" >&2; exit 2 ;;
esac
