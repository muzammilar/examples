#!/bin/sh
# `make benchmark`, part 1 (bench service): `sky-bench --workload uniform_std_v1` against the
# running server. It creates space `db` and model `db.db(k: binary, v: uint64)`, then runs four
# phases over ROWS unique keys from CONNECTIONS connections on THREADS threads: INSERT, UPDATE
# (v += 1), SELECT, DELETE (one query per key each, no pipelining), and drops the space.
# Everything goes to /results/$NAME.txt; bench/report.py (bench-report service) turns it into
# the summary table and JSON.
set -eu

: "${NAME:?}" "${SKYDB_PASSWORD:?}" "${ROWS:?}" "${THREADS:?}" "${CONNECTIONS:?}"
RAW=/results/$NAME.txt

meta() { echo "meta: $1=$2" >>"$RAW"; }

: >"$RAW"
meta rows "$ROWS"
meta threads "$THREADS"
meta connections "$CONNECTIONS"

cmd="sky-bench --workload uniform_std_v1 --rowcount $ROWS --threads $THREADS --connections $CONNECTIONS"
echo "==> $cmd"
echo "=== command: $cmd" >>"$RAW"
# results go to stdout, progress log lines to stderr
timeout 1800 $cmd >>"$RAW" 2>/results/$NAME.log || { echo "sky-bench failed, see results/$NAME.{txt,log}" >&2; exit 1; }
