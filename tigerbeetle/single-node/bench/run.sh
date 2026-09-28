#!/bin/sh
# `make benchmark`, part 1 (bench service, tigerbeetle image): the built-in `tigerbeetle
# benchmark` load generator against the running replica(s). It creates ACCOUNTS accounts
# (ledger 2, time-based ids, so it never collides with client/demo.py's accounts on ledger 1),
# commits TRANSFERS transfers in batches of up to BATCH from CLIENTS clients, then runs 100
# get_account_transfers queries. Everything goes to /results/$NAME.txt; bench/report.py
# (bench-report service) turns it into the summary table and JSON.
set -eu -o pipefail

: "${NAME:?}" "${ADDRESSES:?}" "${TRANSFERS:?}" "${ACCOUNTS:?}" "${CLIENTS:?}" "${BATCH:?}"
RAW=/results/$NAME.txt

meta() { echo "meta: $1=$2" >>"$RAW"; }

: >"$RAW"
meta tigerbeetle_version "$(/tigerbeetle version | sed 's/^TigerBeetle version //')"
meta addresses "$ADDRESSES"
meta replicas "$(echo "$ADDRESSES" | tr ',' '\n' | wc -l | tr -d ' ')"
meta transfers "$TRANSFERS"
meta accounts "$ACCOUNTS"
meta clients "$CLIENTS"
meta batch "$BATCH"

cmd="/tigerbeetle benchmark --addresses=$ADDRESSES --transfer-count=$TRANSFERS \
--account-count=$ACCOUNTS --clients=$CLIENTS --transfer-batch-count=$BATCH"
echo "==> $cmd" | tr -s ' '
echo "=== command: $cmd" | tr -s ' ' >>"$RAW"
# the results go to stdout, the client's log lines (timestamped) to stderr
timeout 1800 $cmd >>"$RAW" 2>&1 || { echo "tigerbeetle benchmark failed, see results/$NAME.txt" >&2; exit 1; }
