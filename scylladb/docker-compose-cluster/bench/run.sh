#!/bin/bash
# `make benchmark`, part 1 (bench service, scylladb/cassandra-stress image): five cassandra-stress
# runs at CONSISTENCY QUORUM against the RF=3 cluster. Everything they print is appended to
# /results/$NAME.txt (the Makefile writes the cluster facts first); bench/report.py
# (bench-report service) turns that file into the summary table and JSON.
set -euo pipefail

: "${NAME:?}" "${DURATION:?}" "${THREADS:?}" "${KEYS:?}" "${LWT_KEYS:?}"
RAW=/results/$NAME.txt
NODES=scylla-1,scylla-2,scylla-3

meta() { echo "meta: $1=$2" >>"$RAW"; }
meta stress_version "$(cassandra-stress version | sed -n 's/^Version: //p')"
meta duration_s "$DURATION"
meta threads "$THREADS"
meta keys "$KEYS"
meta lwt_keys "$LWT_KEYS"
meta consistency QUORUM
meta replication_factor 3

stress() {
	name=$1
	shift
	echo "==> $name: cassandra-stress $*"
	echo "=== workload $name: cassandra-stress $*" >>"$RAW"
	cassandra-stress "$@" -node "$NODES" -rate "threads=$THREADS" -log interval=5s >>"$RAW" 2>&1 ||
		{ echo "cassandra-stress failed, see results/$NAME.txt" >&2; exit 1; }
}

# keyspace1.standard1: one 100-byte column per partition, RF=3 (the Makefile drops it first, so reruns insert again)
SCHEMA=(-schema "replication(strategy=NetworkTopologyStrategy,replication_factor=3)" -col "n=FIXED(1)" "size=FIXED(100)")

# write-heavy: new partitions 1..KEYS in sequence (wraps around if it gets through all of them)
stress write write "duration=${DURATION}s" cl=QUORUM "${SCHEMA[@]}" -pop "seq=1..$KEYS"

# read and mixed pick random partitions among the ones the write run actually created
written=$(awk '/^=== workload write:/ {w=1} w && /^Total partitions/ {gsub(",", "", $4); print $4; exit}' "$RAW")
range=$((written < KEYS ? written : KEYS))
meta read_range "$range"
stress read read "duration=${DURATION}s" cl=QUORUM "${SCHEMA[@]}" -pop "dist=UNIFORM(1..$range)"
stress mixed mixed "ratio(write=1,read=1)" "duration=${DURATION}s" cl=QUORUM "${SCHEMA[@]}" -pop "dist=UNIFORM(1..$range)"

# LWT cost: the same single-row UPDATE, plain and as "IF EXISTS" (Paxos at SERIAL, commit at QUORUM).
# The plain run walks rows 1..LWT_KEYS first, so every LWT finds its row and applies.
stress plain-update user profile=/bench/lwt.yaml "ops(plain=1)" "duration=${DURATION}s" cl=QUORUM -pop "seq=1..$LWT_KEYS"
stress lwt-update user profile=/bench/lwt.yaml "ops(lwt=1)" "duration=${DURATION}s" cl=QUORUM serial-cl=SERIAL -pop "dist=UNIFORM(1..$LWT_KEYS)"
