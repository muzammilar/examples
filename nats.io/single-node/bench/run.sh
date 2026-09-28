#!/bin/sh
# `make benchmark`, part 1 (bench service, nats-box image): `nats bench` workloads against
# the server. Everything they print goes to /results/$NAME.txt; bench/report.py
# (bench-report service) turns that file into the summary table and JSON.
#   core pub/sub   1 publisher -> 1 subscriber, then CLIENTS -> CLIENTS (every subscriber
#                  receives every message), 128 B and 1 KiB messages, MSGS messages per run
#   request/reply  1 requester -> 1 `service serve` responder, REQ_MSGS round trips
#   JetStream      stream "bench" (file storage, R1): synchronous publish (JS_SYNC_MSGS, one
#                  ack per message), asynchronous publish (JS_MSGS, 500 in flight), then a
#                  durable pull consumer fetching them back in batches of 500
set -eu

: "${NAME:?}" "${MSGS:?}" "${CLIENTS:?}" "${REQ_MSGS:?}" "${JS_MSGS:?}" "${JS_SYNC_MSGS:?}"
RAW=/results/$NAME.txt
STREAM=bench
# a subscriber that misses messages (slow consumer) would wait forever
TIMEOUT=300

meta() { echo "meta: $1=$2" >>"$RAW"; }

: >"$RAW"
meta nats_cli_version "$(nats --version)"
meta server_version "$(wget -qO- http://nats:8222/varz | jq -r .version)"
meta msgs "$MSGS"
meta clients "$CLIENTS"
meta req_msgs "$REQ_MSGS"
meta js_msgs "$JS_MSGS"
meta js_sync_msgs "$JS_SYNC_MSGS"

section() {
	echo "==> $1: $2"
	echo "=== workload $1: $2" >>"$RAW"
}
bench() { timeout $TIMEOUT nats bench "$@" --no-progress >>"$RAW" 2>&1; }
fail() { echo "$1 failed, see results/$NAME.txt" >&2; exit 1; }
cleanup() { nats stream rm $STREAM --force >/dev/null 2>&1 || true; }
trap cleanup EXIT

core() { # name clients size
	subj=bench.core.$1
	section "$1" "nats bench sub|pub $subj --clients $2 --size $3 --msgs $MSGS"
	bench sub "$subj" --clients "$2" --size "$3" --msgs "$MSGS" &
	sub=$!
	sleep 1 # subscriptions in place before the first publish
	bench pub "$subj" --clients "$2" --size "$3" --msgs "$MSGS" || fail "$1 publisher"
	wait $sub || fail "$1 subscriber (slow consumer dropped messages?)"
}

core pubsub-1x1-128B 1 128B
core pubsub-1x1-1KiB 1 1KiB
core "pubsub-${CLIENTS}x${CLIENTS}-128B" "$CLIENTS" 128B
core "pubsub-${CLIENTS}x${CLIENTS}-1KiB" "$CLIENTS" 1KiB

section request-reply "nats bench service request bench.svc --clients 1 --size 128B --msgs $REQ_MSGS"
nats bench service serve bench.svc --clients 1 --no-progress >/dev/null 2>&1 &
srv=$!
sleep 1
bench service request bench.svc --clients 1 --size 128B --msgs "$REQ_MSGS" || fail request-reply
kill $srv

cleanup
section js-pub-sync "nats bench js pub sync bench.js --stream $STREAM --storage file --replicas 1 --size 128B --msgs $JS_SYNC_MSGS"
bench js pub sync bench.js --stream $STREAM --create --storage file --replicas 1 --purge \
	--size 128B --msgs "$JS_SYNC_MSGS" || fail js-pub-sync
section js-pub-async "nats bench js pub async bench.js --stream $STREAM --batch 500 --size 128B --msgs $JS_MSGS"
bench js pub async bench.js --stream $STREAM --create --storage file --replicas 1 --purge \
	--batch 500 --size 128B --msgs "$JS_MSGS" || fail js-pub-async
section js-fetch "nats bench js fetch --stream $STREAM --batch 500 --msgs $JS_MSGS"
bench js fetch --stream $STREAM --batch 500 --msgs "$JS_MSGS" || fail js-fetch
echo "==> removing stream $STREAM"
