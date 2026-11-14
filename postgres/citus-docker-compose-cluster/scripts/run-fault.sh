#!/bin/sh
set -eu
fault=${1:-primary}
docker compose run --rm -T workload load > ${RESULTS_DIR:-results/manual}/workload.log 2>&1 &
load_pid=$!
trap 'kill "$load_pid" 2>/dev/null || true' EXIT INT TERM
ready=false
for attempt in $(seq 120); do
 if [ -f ${RESULTS_DIR:-results/manual}/acknowledged.jsonl ] && [ "$(wc -l < ${RESULTS_DIR:-results/manual}/acknowledged.jsonl)" -ge 20 ]; then ready=true;break;fi
 kill -0 "$load_pid" 2>/dev/null || { cat ${RESULTS_DIR:-results/manual}/workload.log;exit 1; }
 sleep 1
done
[ "$ready" = true ] || { echo 'Workload never reached fault threshold' >&2;exit 1; }
kill -0 "$load_pid" 2>/dev/null || { echo 'Workload completed before fault injection' >&2;exit 1; }
fault_status=0
case "$fault" in
 primary) sh scripts/failover.sh ;;
 replica-lag) sh scripts/replica-lag.sh ;;
 scale-out) sh scripts/rebalance.sh ;;
 scale-in) sh scripts/drain.sh ;;
 *) echo 'Unknown fault' >&2;exit 1 ;;
esac || fault_status=$?
wait "$load_pid" || { cat ${RESULTS_DIR:-results/manual}/workload.log;exit 1; }
trap - EXIT INT TERM
exit "$fault_status"
