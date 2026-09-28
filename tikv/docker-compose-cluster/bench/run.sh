#!/bin/sh
# `make benchmark`, part 1 (bench service, bench/Dockerfile: go-ycsb with its TiKV driver).
# go-ycsb talks to PD for routing and timestamps and to the TiKV stores directly, once per
# API: RawKV (tikv.type=raw: single-key Get/Put, no MVCC, no transactions) and TxnKV
# (tikv.type=txn: every read gets a start_ts from PD, every update is a Percolator commit,
# one-phase or async commit where possible). For each: load RECORDS rows (10 fields of 100
# bytes) into table ycsb_<api>, then YCSB workload A (50% reads, 50% updates) and C (100%
# reads), OPERATIONS operations each with THREADS threads, uniform key distribution.
# With API V1 raw and transactional keys must not overlap, hence the two tables (key
# prefixes ycsb_raw: and ycsb_txn:); the Makefile deletes both afterwards.
# Everything goes to /results/$NAME.txt; bench/report.py turns it into a table and JSON.
set -eu

: "${NAME:?}" "${THREADS:?}" "${RECORDS:?}" "${OPERATIONS:?}"
RAW=/results/$NAME.txt
PD=pd0:2379,pd1:2379,pd2:2379
PD_API=http://pd0:2379/pd/api/v1

meta() { echo "meta: $1=$2" >>"$RAW"; }
fail() { echo "$1 failed, see results/$NAME.txt" >&2; exit 1; }
json_field() { sed -n "s/.*\"$1\": *\"\{0,1\}\([^\",]*\).*/\1/p" | head -n 1; }
elapsed() { echo "$(date +%s.%N) $1" | awk '{printf "%.2f", $1 - $2}'; }
ycsb() {
	# --interval: no periodic reports, just the totals after "Run finished"
	go-ycsb "$@" tikv -p tikv.pd=$PD -p recordcount="$RECORDS" -p operationcount="$OPERATIONS" \
		--threads "$THREADS" --interval 86400 >>"$RAW" 2>&1
}

: >"$RAW"
meta go_ycsb_version "$GO_YCSB_VERSION"
meta pd_version "$(wget -qO- $PD_API/version | json_field version)"
meta tikv_version "$(wget -qO- $PD_API/stores | json_field version)"
meta tikv_stores_up "$(wget -qO- $PD_API/stores | grep -c '"state_name": "Up"')"
meta pd_members "$(wget -qO- $PD_API/health | grep -c '"health": true')"
meta max_replicas "$(wget -qO- $PD_API/config/replicate | json_field max-replicas)"
meta threads "$THREADS"
meta records "$RECORDS"
meta operations "$OPERATIONS"

for api in raw txn; do
	props="-p tikv.type=$api --table ycsb_$api"
	echo "==> $api: load $RECORDS records"
	start=$(date +%s.%N)
	echo "=== load $api: go-ycsb load tikv $props -P workloads/workloada" >>"$RAW"
	ycsb load $props -P /workloads/workloada || fail "$api load"
	echo "load: $api seconds=$(elapsed "$start")" >>"$RAW"
	for w in a c; do
		echo "==> $api workload $w: $OPERATIONS operations, $THREADS threads"
		echo "=== workload ${api}_$w: go-ycsb run tikv $props -P workloads/workload$w" >>"$RAW"
		ycsb run $props -P /workloads/workload$w || fail "$api workload $w"
	done
done
