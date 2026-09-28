#!/bin/sh
# `make benchmark`, part 1 (bench service, aerospike-tools image): three asbench workloads
# against set test.bench. Everything asbench prints goes to /results/$NAME.txt;
# bench/report.py (bench-report service) turns that file into the summary table and JSON.
set -eu

: "${NAME:?}" "${DURATION:?}" "${THREADS:?}" "${KEYS:?}" "${OBJECT_SPEC:?}"
HOST=aerospike-1
NODES="aerospike-1 aerospike-2 aerospike-3"
RAW=/results/$NAME.txt
HIST=$(mktemp -d)

meta() { echo "meta: $1=$2" >>"$RAW"; }

: >"$RAW"
meta asbench_version "$(asbench --version | sed -n 's/^Version //p')"
meta server_version "$(asinfo -h $HOST -v build)"
meta cluster_size "$(asinfo -h $HOST -v statistics -l | sed -n 's/^cluster_size=//p')"
meta replication_factor "$(asinfo -h $HOST -v namespace/test -l | sed -n 's/^replication-factor=//p')"
meta storage "$(asinfo -h $HOST -v namespace/test -l | sed -n 's/^storage-engine=//p')"
meta duration_s "$DURATION"
meta threads "$THREADS"
meta keys "$KEYS"
meta object_spec "$OBJECT_SPEC"

# start from an empty set, so the insert workload creates every record
echo "==> truncating set test.bench"
asinfo -h $HOST -v 'truncate:namespace=test;set=bench' >/dev/null
i=0
while :; do
	n=0
	for h in $NODES; do
		o=$(asinfo -h "$h" -v sets/test/bench | tr ':;' '\n\n' | sed -n 's/^objects=//p')
		n=$((n + ${o:-0}))
	done
	[ "$n" = 0 ] && break
	i=$((i + 1))
	[ $i -ge 60 ] && { echo "set test.bench still holds $n records after 60 s" >&2; exit 1; }
	sleep 1
done

# -L prints a cumulative HDR line per op each second (µs: min, max, p50, p95, p99, p99.9);
# --hdr-hist dumps the final histogram, whose first line holds the exact start/end time.
run() {
	name=$1
	shift
	echo "==> $name: asbench $*"
	echo "=== workload $name: asbench $*" >>"$RAW"
	rm -f "$HIST"/*
	asbench -h $HOST -n test -s bench -k "$KEYS" -o "$OBJECT_SPEC" -z "$THREADS" \
		-L --percentiles 50,95,99,99.9 --hdr-hist "$HIST" "$@" >>"$RAW" 2>&1 ||
		{ echo "asbench failed, see results/$NAME.txt" >&2; exit 1; }
	for f in "$HIST"/*.hdrhist; do
		op=$(basename "$f")
		op=${op%%_*}
		echo "cumulative: op=$op interval=$(grep -E '^[0-9]' "$f" | tail -n 1 | cut -d, -f1,2)" >>"$RAW"
		echo "--- histogram op=$op (µs)" >>"$RAW"
		cat "${f%.hdrhist}.txt" >>"$RAW"
	done
}

run insert -w I
run read -w RU,100 -t "$DURATION"
run read-update -w RU,80 -t "$DURATION"
