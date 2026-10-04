#!/bin/bash
# `make scale-demo`: load running through 3 -> 5 -> 3 brokers. Phases (BASELINE / SETTLE seconds):
#   baseline, scale-out (until balanced), settle, scale-in (until decommissioned), settle;
# then the load client stops, reads the topic back and checks every acked record is there once.
# Summary per phase from the load client's per-second lines: avg acked/s, avg consumed/s,
# worst per-second p99 and max ack latency, failed records.
set -euo pipefail
BASELINE=${BASELINE:-45}; SETTLE=${SETTLE:-30}
mkdir -p results
OUT=results/scale-$(date -u +%Y%m%dT%H%M%SZ)
mark() { echo "$(date -u +%T) $1" >>"$OUT.phases"; echo "==== [$(date -u +%T)] $1"; }

docker exec redpanda-cc-0 rpk cluster health >/dev/null || { echo 'cluster not healthy; run `make up`'; exit 1; }
docker rm -f redpanda-cc-load >/dev/null 2>&1 || true
docker compose --profile tools build -q load
docker compose --profile tools run -d --name redpanda-cc-load -e TOPIC=scale -e PARTITIONS=24 -e RATE=${RATE:-2000} -e RECORD_SIZE=${RECORD_SIZE:-512} -e DURATION=30m load >/dev/null
sleep 2
mark baseline;  sleep "$BASELINE"
mark scale-out; bash scripts/scale.sh out
mark settle-5;  sleep "$SETTLE"
mark scale-in;  bash scripts/scale.sh in
mark settle-3;  sleep "$SETTLE"
mark end
docker kill --signal TERM redpanda-cc-load >/dev/null
rc=$(docker wait redpanda-cc-load)
docker logs redpanda-cc-load >"$OUT.load.log" 2>&1
docker rm redpanda-cc-load >/dev/null
grep -E '^load:|^produced|^slowest|^read back|^CHECK' "$OUT.load.log"
echo
awk 'NR == FNR { t[NR] = $1; p[NR] = $2; n = NR; next }
	/acked .*\/s/ { ts = substr($1, 2); ph = ""
		for (i = 1; i < n; i++) if (ts >= t[i] && ts < t[i + 1]) ph = p[i]
		if (ph == "") next
		match($0, /acked +[0-9]+/); a = substr($0, RSTART + 6, RLENGTH - 6) + 0
		match($0, /failed +[0-9]+/); f = substr($0, RSTART + 7, RLENGTH - 7) + 0
		match($0, /consumed +[0-9]+/); c = substr($0, RSTART + 9, RLENGTH - 9) + 0
		match($0, /p99 +[0-9.]+/); q = substr($0, RSTART + 4, RLENGTH - 4) + 0
		match($0, /max +[0-9.]+/); m = substr($0, RSTART + 4, RLENGTH - 4) + 0
		S[ph] += 1; A[ph] += a; C[ph] += c; F[ph] += f
		if (q > Q[ph]) Q[ph] = q; if (m > M[ph]) M[ph] = m }
	END { printf "%-10s %6s %10s %12s %12s %12s %7s\n", "phase", "secs", "acked/s", "consumed/s", "worst p99", "max ack", "failed"
		for (i = 1; i < n; i++) { ph = p[i]; if (!S[ph]) continue
			printf "%-10s %6d %10.0f %12.0f %9.1f ms %9.1f ms %7d\n", ph, S[ph], A[ph] / S[ph], C[ph] / S[ph], Q[ph], M[ph], F[ph] } }' \
	"$OUT.phases" "$OUT.load.log" | tee "$OUT.summary"
echo "raw: $OUT.load.log, $OUT.phases"
exit "$rc"
