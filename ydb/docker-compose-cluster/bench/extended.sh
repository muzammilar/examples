#!/bin/sh
# `make benchmark-extended`: the longer look at the cluster, under the same resource budget as
# `make benchmark` (the Makefile applies and restores it). Four parts, each written to the raw
# log and to one JSON summary in results/ (gitignored):
#   scaling     kv upsert, kv select and stock put-rand-order at each of EXT_THREADS threads for
#               EXT_TIME seconds: where throughput saturates and retries/errors start
#   failover    kv upsert + kv select side by side for EXT_FO_TIME seconds, EXT_FO_THREADS threads
#               each, reporting every 10 s; mid-run ydb-storage-3 (zone-c) and later ydb-dynamic-2
#               are stopped, then both started again; afterwards the time until the self-check
#               is GOOD with all 5 nodes alive
#   breadth     `workload tpcc` (EXT_TPCC_WAREHOUSES warehouses, EXT_TPCC_TIME run) and TPC-H
#               Q1/Q6 on row- vs column-store tables at scale EXT_TPCH_SCALE, each step capped at
#               EXT_TIMEBOX seconds (skipped, and marked so, when it does not finish in time)
#   load        per-dynamic-node query share (Prometheus) and per-container CPU / memory from
#               `docker stats` samples every 5 s, for the scaling and failover parts
# Like `make benchmark`, the CLI runs inside ydb-storage-1 and shares its CPU/memory cap.
set -eu
cd "$(dirname "$0")/.."

: "${EXT_TIME:=30}" "${EXT_THREADS:=1 4 16 64}" "${EXT_KV_ROWS:=10000}" "${EXT_PRODUCTS:=100}"
: "${EXT_ORDERS:=10000}" "${EXT_PARTITIONS:=4}" "${EXT_FO_TIME:=180}" "${EXT_FO_THREADS:=8}"
: "${EXT_TPCC_WAREHOUSES:=20}" "${EXT_TPCC_TIME:=120}" "${EXT_TPCH_SCALE:=0.1}" "${EXT_TIMEBOX:=900}"
: "${EXT_PARTS:=scaling failover breadth}"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p results
raw=results/ydb-extended-$stamp.log
json=results/ydb-extended-$stamp.json
stats=results/.ydb-extended-$stamp.stats
tmp=results/.ext.$$
NODES="ydb-storage-1 ydb-storage-2 ydb-storage-3 ydb-dynamic-1 ydb-dynamic-2"
VIEWER=localhost:8765/viewer/json

ydb() { docker exec -i ydb-storage-1 /ydb -e grpc://ydb-dynamic-1:2136 -d /Root/testdb "$@"; }
log() { echo "$*" | tee -a "$raw"; }
now() { date +%s; }
KV_KEYS=$((EXT_KV_ROWS * 10))
kv="--max-first-key $KV_KEYS"

# per-dynamic-node KQP query counters from Prometheus ("node count" lines)
node_queries() {
	curl -sfg --max-time 10 'localhost:9090/federate?match[]=kqp_Requests_QueryExecute{role="dynamic"}' |
		sed -n 's/.*instance="\([^:"]*\):[0-9]*".*} \([0-9.e+]*\).*/\1 \2/p' | sort || true
}
# JSON object {"node": delta, ...} from two node_queries snapshots
node_delta() {
	printf '%s\n%s\n' "$1" "$2" | awk 'NF == 2 { if ($1 in a) d[$1] = $2 - a[$1]; else a[$1] = $2 }
		($1 in a) && $2 < a[$1] { d[$1] = $2 }  # counter reset by a restart: count since then
		END { for (n in d) printf "%s\"%s\": %d", (k++ ? ", " : ""), n, d[n] }'
}
# `docker stats` every 5 s into $stats as "epoch part name cpu% mem_mib" until killed
sampler() {
	while :; do
		t=$(now)
		docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' $NODES 2>/dev/null |
			awk -v t="$t" -v p="$1" '{ cpu = $2; sub(/%/, "", cpu); m = $3; v = m + 0
				if (m ~ /GiB/) v *= 1024; else if (m ~ /KiB/) v /= 1024
				printf "%s %s %s %s %.0f\n", t, p, $1, cpu, v }' >>"$stats"
		sleep 5
	done
}
# JSON object per container {"name": {"cpu_avg": .., "cpu_max": .., "mem_max_mib": ..}} for a part
stats_json() {
	awk -v p="$1" '$2 == p { n[$3]++; s[$3] += $4; if ($4 > c[$3]) c[$3] = $4; if ($5 > m[$3]) m[$3] = $5 }
		END { for (k in n) printf "%s\"%s\": {\"cpu_avg_pct\": %.0f, \"cpu_max_pct\": %.0f, \"mem_max_mib\": %d}",
			(j++ ? ", " : ""), k, s[k] / n[k], c[k], m[k] }' "$stats" 2>/dev/null
}
stats_table() {
	awk -v p="$1" '$2 == p { n[$3]++; s[$3] += $4; if ($4 > c[$3]) c[$3] = $4; if ($5 > m[$3]) m[$3] = $5 }
		END { for (k in n) printf "  %-14s cpu avg %4.0f%%  max %4.0f%%  mem max %5d MiB\n", k, s[k] / n[k], c[k], m[k] }' \
		"$stats" 2>/dev/null | sort
}
# the "Total ..." summary of a `workload ... run` output -> JSON object (plus a table row on line 2)
summary() { # summary <file> <workload> <threads>
	awk -v w="$2" -v t="$3" '
		$1 == "Total" && $2 == "Txs" { getline; txs = $2; tps = $3; re = $4; er = $5; p50 = $6; p95 = $7; p99 = $8; pmax = $9; found = 1 }
		END {
			if (!found) exit 1
			printf "{\"workload\":\"%s\",\"threads\":%d,\"txs\":%s,\"txs_per_sec\":%s,\"retries\":%s,\"errors\":%s,\"p50_ms\":%s,\"p95_ms\":%s,\"p99_ms\":%s,\"max_ms\":%s}\n", w, t, txs, tps, re, er, p50, p95, p99, pmax
			printf "%-22s %7s %9s %9s %8s %7s %7s %7s %7s\n", w, t, tps, re, er, p50, p95, p99, pmax
		}' "$1"
}
# timebox SECS CMD...: run CMD, give up (status 124) after SECS; macOS has no timeout(1).
# The CLI is started inside the container, so it is killed there with pkill.
timebox() {
	secs=$1
	shift
	"$@" </dev/null &
	cmd=$!
	(sleep "$secs"; kill $cmd 2>/dev/null && docker exec ydb-storage-1 pkill -f "/ydb .*workload" 2>/dev/null) >/dev/null 2>&1 &
	guard=$!
	rc=0
	wait $cmd || rc=$?
	kill $guard 2>/dev/null || true
	return $rc
}
health() { # "GOOD 5" = self-check result and alive nodes, as seen by ydb-storage-1's viewer
	h=$(curl -s --max-time 20 $VIEWER/healthcheck | sed -n 's/.*"self_check_result":"\([A-Z_]*\)".*/\1/p')
	a=$(curl -s --max-time 20 $VIEWER/cluster | sed -n 's/.*"NodesAlive":\([0-9]*\).*/\1/p')
	echo "${h:-?} ${a:-?}"
}

# wait (idle) up to EXT_TIMEBOX for the self-check to be GOOD with all 5 nodes alive; log the
# issues if it is not (a PDisk that failed to open at startup shows up here, for example)
wait_good() {
	t=$(now)
	while [ $(($(now) - t)) -lt "$EXT_TIMEBOX" ]; do
		[ "$(health)" = "GOOD 5" ] && break
		sleep 10
	done
	h=$(health)
	log "  self-check ${h% *}, ${h#* } nodes alive (after $(($(now) - t))s)"
	[ "$h" = "GOOD 5" ] || curl -s --max-time 20 $VIEWER/healthcheck | tr '{' '\n' |
		grep -o '"message":"[^"]*"' | sort | uniq -c | tee -a "$raw"
}

cleanup() {
	[ -n "${spid:-}" ] && kill "$spid" 2>/dev/null || true
	docker compose start ydb-storage-3 ydb-dynamic-2 >/dev/null 2>&1 || true
	ydb workload kv clean >>"$raw" 2>&1 || true
	ydb workload stock clean >>"$raw" 2>&1 || true
	ydb workload tpcc clean >>"$raw" 2>&1 || true
	ydb workload tpch -p ext_tpch_row clean >>"$raw" 2>&1 || true
	ydb workload tpch -p ext_tpch_column clean >>"$raw" 2>&1 || true
	rm -f "$tmp".*
	echo "workload tables removed"
}
trap cleanup EXIT
: >"$raw"
: >"$stats"

scaling_json="null" scaling_nodes="{}" scaling_stats="{}"
failover_json="null" failover_stats="{}" failover_nodes="{}"
tpcc_json='{"skipped": "not run"}' tpch_json='{"skipped": "not run"}'

part() { case " $EXT_PARTS " in *" $1 "*) return 0 ;; esac; return 1; }

health_start=$(health)
log "==> cluster health before the run"
wait_good
health_start=$(health)

# ---------------------------------------------------------------- scaling
if part scaling; then
	log "==> scaling: kv upsert / kv select / stock put-rand-order at $EXT_THREADS threads, ${EXT_TIME}s each"
	ydb workload kv init --min-partitions "$EXT_PARTITIONS" --init-upserts "$EXT_KV_ROWS" $kv >>"$raw" 2>&1
	ydb workload stock init --min-partitions "$EXT_PARTITIONS" -p "$EXT_PRODUCTS" -q 1000000 -o "$EXT_ORDERS" >>"$raw" 2>&1
	q0=$(node_queries)
	sampler scaling & spid=$!
	rows="" table=""
	for w in "kv upsert" "kv select" "stock put-rand-order"; do
		set -- $w
		extra=""
		[ "$1" = kv ] && extra=$kv
		for t in $EXT_THREADS; do
			log "run: $w, $t threads, ${EXT_TIME}s"
			ydb workload "$1" run "$2" --seconds "$EXT_TIME" --threads "$t" --quiet $extra >"$tmp.run" 2>&1 || true
			{ echo "=== scaling $w threads=$t"; cat "$tmp.run"; } >>"$raw"
			if parsed=$(summary "$tmp.run" "$w" "$t"); then
				rows="${rows:+$rows,}$(echo "$parsed" | sed -n 1p)"
				table="$table
$(echo "$parsed" | sed -n 2p)"
			else
				log "   no summary (see raw log)"
			fi
		done
	done
	kill $spid; spid=""
	sleep 16 # one Prometheus scrape interval
	scaling_nodes="{$(node_delta "$q0" "$(node_queries)")}"
	scaling_json="[$rows]"
	scaling_stats="{$(stats_json scaling)}"
	{
		echo
		printf '%-22s %7s %9s %9s %8s %7s %7s %7s %7s\n' workload threads "txs/s" retries errors "p50 ms" "p95 ms" "p99 ms" "max ms"
		echo "$table" | sed '/^$/d'
		echo "queries per dynamic node: $scaling_nodes"
		echo "docker stats (every 5 s):"
		stats_table scaling
	} | tee -a "$raw"
fi

# ---------------------------------------------------------------- failover
if part failover; then
	log "==> failover: kv upsert + kv select, $EXT_FO_THREADS threads each, ${EXT_FO_TIME}s, 10 s windows"
	ydb workload kv init --min-partitions "$EXT_PARTITIONS" --init-upserts "$EXT_KV_ROWS" $kv >>"$raw" 2>&1 || true
	# the failure test only means something on a healthy cluster: wait (idle) for GOOD first
	wait_good
	pre=$(health)
	q0=$(node_queries)
	sampler failover & spid=$!
	t0=$(now)
	for op in upsert select; do
		ydb workload kv run $op --seconds "$EXT_FO_TIME" --threads "$EXT_FO_THREADS" --window 10 $kv </dev/null >"$tmp.$op" 2>&1 &
		eval "pid_$op=\$!"
	done
	# events at 1/6, 2/6 and 3/6 of the run; health sampled every 10 s
	step=$((EXT_FO_TIME / 6))
	events="" hl=""
	for i in $(seq 1 $((EXT_FO_TIME / 10))); do
		el=$(($(now) - t0))
		[ $el -ge $step ] && [ -z "${ev1:-}" ] && { docker compose stop ydb-storage-3 >/dev/null 2>&1; ev1=$(($(now) - t0)); log "  t=${ev1}s stopped ydb-storage-3 (zone-c)"; }
		[ $el -ge $((2 * step)) ] && [ -z "${ev2:-}" ] && { docker compose stop ydb-dynamic-2 >/dev/null 2>&1; ev2=$(($(now) - t0)); log "  t=${ev2}s stopped ydb-dynamic-2"; }
		[ $el -ge $((3 * step)) ] && [ -z "${ev3:-}" ] && { docker compose start ydb-storage-3 ydb-dynamic-2 >/dev/null 2>&1; ev3=$(($(now) - t0)); log "  t=${ev3}s started ydb-storage-3 and ydb-dynamic-2"; }
		h=$(health)
		hl="${hl:+$hl, }{\"t_s\": $(($(now) - t0)), \"self_check\": \"${h% *}\", \"nodes_alive\": \"${h#* }\"}"
		log "  t=$(($(now) - t0))s health: $h"
		[ $(($(now) - t0)) -ge "$EXT_FO_TIME" ] && break
		sleep 10
	done
	wait $pid_upsert || true
	wait $pid_select || true
	# time to heal: first health sample GOOD with 5 nodes alive after the restart, from the samples
	# taken during the run, else by polling afterwards
	heal=$(echo "$hl" | tr '}' '\n' | sed -n 's/.*"t_s": \([0-9]*\), "self_check": "GOOD", "nodes_alive": "5".*/\1/p' |
		awk -v e="${ev3:-999999}" '$1 >= e { print $1 - e; exit }')
	[ -n "$heal" ] || heal=null
	[ "$heal" != null ] || while [ $(($(now) - t0)) -lt $((EXT_FO_TIME + EXT_TIMEBOX)) ]; do
		[ "$(health)" = "GOOD 5" ] && { heal=$(($(now) - t0 - ev3)); break; }
		sleep 10
	done
	log "  healed (GOOD, 5 nodes alive) ${heal}s after the restart"
	kill $spid; spid=""
	for op in upsert select; do { echo "=== failover kv $op"; cat "$tmp.$op"; } >>"$raw"; done
	# window lines: "N Txs Txs/Sec Retries Errors p50 p95 p99 pMax" -> by window
	timeline=$(for op in upsert select; do
		awk -v op=$op '$1 ~ /^[0-9]+$/ && NF >= 9 && !tot { print op, $1 * 10, $3, $4, $5, $6, $8 } $1 == "Total" { tot = 1 }' "$tmp.$op"
	done | sort -k2,2n -k1,1)
	sleep 16
	failover_nodes="{$(node_delta "$q0" "$(node_queries)")}"
	failover_stats="{$(stats_json failover)}"
	tl_json=$(echo "$timeline" | awk 'NF == 7 { printf "%s{\"op\": \"%s\", \"t_s\": %d, \"txs_per_sec\": %s, \"retries\": %s, \"errors\": %s, \"p50_ms\": %s, \"p99_ms\": %s}", (n++ ? ", " : ""), $1, $2, $3, $4, $5, $6, $7 }')
	u_tot=$(summary "$tmp.upsert" "kv upsert" "$EXT_FO_THREADS" | sed -n 1p) || u_tot=null
	s_tot=$(summary "$tmp.select" "kv select" "$EXT_FO_THREADS" | sed -n 1p) || s_tot=null
	failover_json="{\"health_before\": \"$pre\", \"events\": {\"storage_3_stopped_s\": ${ev1:-null}, \"dynamic_2_stopped_s\": ${ev2:-null}, \"both_started_s\": ${ev3:-null}, \"healed_s_after_start\": $heal},
    \"totals\": [${u_tot:-null}, ${s_tot:-null}], \"windows\": [$tl_json], \"health\": [$hl]}"
	{
		echo
		echo "per 10 s window (t = end of window; events: storage-3 stopped t=${ev1:-?}s, dynamic-2 stopped t=${ev2:-?}s, both started t=${ev3:-?}s)"
		printf '%-7s %5s %9s %8s %7s %7s %7s\n' op t_s "txs/s" retries errors "p50 ms" "p99 ms"
		echo "$timeline" | awk 'NF == 7 { printf "%-7s %5s %9s %8s %7s %7s %7s\n", $1, $2, $3, $4, $5, $6, $7 }'
		echo "queries per dynamic node: $failover_nodes"
		echo "docker stats (every 5 s):"
		stats_table failover
	} | tee -a "$raw"
fi

# ---------------------------------------------------------------- breadth
if part breadth; then
	w=$EXT_TPCC_WAREHOUSES
	# `workload stock` also has a table called stock
	ydb workload kv clean >>"$raw" 2>&1 || true
	ydb workload stock clean >>"$raw" 2>&1 || true
	log "==> tpcc: $w warehouses, ${EXT_TPCC_TIME}s run (timebox ${EXT_TIMEBOX}s per step)"
	t=$(now)
	if timebox "$EXT_TIMEBOX" docker exec -i ydb-storage-1 /ydb -e grpc://ydb-dynamic-1:2136 -d /Root/testdb workload tpcc init -w "$w" >>"$raw" 2>&1 &&
		timebox "$EXT_TIMEBOX" docker exec -i ydb-storage-1 /ydb -e grpc://ydb-dynamic-1:2136 -d /Root/testdb workload tpcc import -w "$w" --no-tui >>"$raw" 2>&1; then
		load_s=$(($(now) - t))
		log "   loaded in ${load_s}s"
		timebox $((EXT_TPCC_TIME + EXT_TIMEBOX)) docker exec -i ydb-storage-1 /ydb -e grpc://ydb-dynamic-1:2136 -d /Root/testdb \
			workload tpcc run -w "$w" --warmup 30s -t "${EXT_TPCC_TIME}s" --no-tui >"$tmp.tpcc" 2>&1 || true
		{ echo "=== tpcc run"; cat "$tmp.tpcc"; } >>"$raw"
		tpmc=$(sed -n 's/.*tpmC: *\([0-9.]*\).*/\1/p' "$tmp.tpcc" | tail -1)
		eff=$(sed -n 's/.*Efficiency: *\([0-9.]*\)%.*/\1/p' "$tmp.tpcc" | tail -1)
		tx=$(awk -F'│' 'NF > 6 && $2 !~ /Transaction/ { gsub(/ /, ""); if ($2 != "") printf "%s{\"tx\": \"%s\", \"ok\": %s, \"failed\": %s, \"p50_ms\": %s, \"p90_ms\": %s, \"p99_ms\": %s}", (n++ ? ", " : ""), $2, $3, $4, ($5 == "" ? "null" : $5), ($6 == "" ? "null" : $6), ($7 == "" ? "null" : $7) }' "$tmp.tpcc")
		tpcc_json="{\"warehouses\": $w, \"load_s\": $load_s, \"run_s\": $EXT_TPCC_TIME, \"tpmC\": ${tpmc:-null}, \"efficiency_pct\": ${eff:-null}, \"transactions\": [$tx]}"
		grep -E 'tpmC|Efficiency|│' "$tmp.tpcc" | tail -16 | tee -a "$raw" >/dev/null
		sed -n '/┌/,/┘/p' "$tmp.tpcc"
		echo "tpmC ${tpmc:-?}, efficiency ${eff:-?}% (the CLI runs TPC-C with keying/think times: at most ~12.9 tpmC per warehouse)"
	else
		tpcc_json="{\"warehouses\": $w, \"skipped\": \"init/import failed or took over ${EXT_TIMEBOX}s (see the raw log)\"}"
		log "   tpcc skipped: init/import failed or took over ${EXT_TIMEBOX}s (see the raw log)"
	fi
	ydb workload tpcc clean >>"$raw" 2>&1 || true

	log "==> tpch Q1/Q6: row vs column store, scale $EXT_TPCH_SCALE, 3 iterations"
	tpch_rows=""
	for store in row column; do
		p=ext_tpch_$store
		t=$(now)
		if timebox "$EXT_TIMEBOX" docker exec -i ydb-storage-1 /ydb -e grpc://ydb-dynamic-1:2136 -d /Root/testdb workload tpch -p $p init --store $store --scale "$EXT_TPCH_SCALE" >>"$raw" 2>&1 &&
			timebox "$EXT_TIMEBOX" docker exec -i ydb-storage-1 /ydb -e grpc://ydb-dynamic-1:2136 -d /Root/testdb workload tpch -p $p import generator --scale "$EXT_TPCH_SCALE" >>"$raw" 2>&1; then
			load_s=$(($(now) - t))
			ydb workload tpch -p $p run --include 1,6 --iterations 3 >"$tmp.tpch" 2>&1 || true
			{ echo "=== tpch $store"; cat "$tmp.tpch"; } >>"$raw"
			q=$(awk -F'│' '$2 ~ /Query[0-9]/ { gsub(/ /, ""); printf "%s\"%s\": {\"cold_s\": %s, \"min_s\": %s, \"median_s\": %s}", (n++ ? ", " : ""), $2, $3, $4, $7 }' "$tmp.tpch")
			tpch_rows="${tpch_rows:+$tpch_rows, }\"$store\": {\"load_s\": $load_s, \"queries\": {$q}}"
			log "   $store: loaded in ${load_s}s; $(awk -F'│' '$2 ~ /Query[0-9]/ { gsub(/ /, ""); printf "%s cold %ss median %ss  ", $2, $3, $7 }' "$tmp.tpch")"
		else
			tpch_rows="${tpch_rows:+$tpch_rows, }\"$store\": {\"skipped\": \"init/import failed or took over ${EXT_TIMEBOX}s\"}"
			log "   $store: skipped, init/import failed or took over ${EXT_TIMEBOX}s (see the raw log)"
		fi
		ydb workload tpch -p $p clean >>"$raw" 2>&1 || true
	done
	tpch_json="{\"scale\": $EXT_TPCH_SCALE, \"queries\": \"Q1, Q6\", \"iterations\": 3, $tpch_rows}"
fi

cli_version=$(docker exec ydb-storage-1 /ydb version 2>/dev/null | sed 's/^YDB CLI //')
image=$(docker inspect --format '{{.Config.Image}}' ydb-storage-1)
cat >"$json" <<JSON
{
  "timestamp": "$stamp",
  "system": "ydb-docker-compose-cluster",
  "benchmark": "extended",
  "versions": {"ydb_cli": "$cli_version", "image": "$image"},
  "machine": {"docker_cpus": $(docker info --format '{{.NCPU}}'), "docker_mem_bytes": $(docker info --format '{{.MemTotal}}'),
              "docker_arch": "$(docker info --format '{{.Architecture}}')", "host": "$(uname -sm)", "ydb_emulated": true},
  "limits": ${BENCH_LIMITS:-null},
  "health_at_start": "$health_start",
  "parameters": {"scaling_time_s": $EXT_TIME, "scaling_threads": "$EXT_THREADS", "kv_rows": $EXT_KV_ROWS,
                 "failover_time_s": $EXT_FO_TIME, "failover_threads_per_op": $EXT_FO_THREADS,
                 "tpcc_warehouses": $EXT_TPCC_WAREHOUSES, "tpcc_time_s": $EXT_TPCC_TIME, "tpch_scale": $EXT_TPCH_SCALE,
                 "timebox_s": $EXT_TIMEBOX},
  "scaling": {"results": $scaling_json, "queries_per_dynamic_node": $scaling_nodes, "docker_stats": $scaling_stats},
  "failover": {"run": $failover_json, "queries_per_dynamic_node": $failover_nodes, "docker_stats": $failover_stats},
  "tpcc": $tpcc_json,
  "tpch": $tpch_json
}
JSON
echo
echo "raw: $raw  json: $json"
