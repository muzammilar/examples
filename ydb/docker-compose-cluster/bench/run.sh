#!/bin/sh
# YDB CLI built-in workloads against /Root/testdb: `workload kv` (single-row upserts and
# point selects) and `workload stock` (put-rand-order: a multi-table read-write
# transaction; rand-user-hist: a read over a secondary index). Each runs for
# BENCH_TIME seconds at each of BENCH_THREADS. The CLI runs inside ydb-storage-1 and
# discovers both dynamic nodes, spreading sessions over them (the per-node query counts
# from Prometheus are printed at the end). Prints a table, saves raw
# output and a JSON summary to results/ (gitignored), then drops the workload tables.
# Env (set by `make benchmark`): BENCH_TIME, BENCH_THREADS, BENCH_KV_ROWS,
# BENCH_PRODUCTS, BENCH_ORDERS, BENCH_PARTITIONS.
set -eu
cd "$(dirname "$0")/.."

stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p results
raw=results/ydb-workload-$stamp.log
json=results/ydb-workload-$stamp.json

ydb() { docker exec -i ydb-storage-1 /ydb -e grpc://ydb-dynamic-1:2136 -d /Root/testdb "$@"; }
# kv keys are drawn from [0, KV_KEYS) so selects hit rows written by init/upsert
KV_KEYS=$((BENCH_KV_ROWS * 10))
kv="--max-first-key $KV_KEYS"

# per-dynamic-node KQP query counters, read from Prometheus' /federate as text
node_queries() {
	curl -sfg --max-time 10 'localhost:9090/federate?match[]=kqp_Requests_QueryExecute{role="dynamic"}' |
		sed -n 's/.*instance="\([^:"]*\):[0-9]*".*} \([0-9.e+]*\).*/\1 \2/p' | sort || true
}

clean() {
	ydb workload kv clean >>"$raw" 2>&1 || true
	ydb workload stock clean >>"$raw" 2>&1 || true
}
trap 'clean; echo "workload tables removed"' EXIT
clean # leftovers from an interrupted run

echo "==> endpoints" | tee -a "$raw"
ydb discovery list | tee -a "$raw"
endpoints=$(ydb discovery list | awk '{ print $1 }' | sed 's|.*//||' | paste -sd, -)

echo "init: kv $BENCH_KV_ROWS rows, stock $BENCH_PRODUCTS products / $BENCH_ORDERS orders, $BENCH_PARTITIONS partitions per table"
ydb workload kv init --min-partitions "$BENCH_PARTITIONS" --init-upserts "$BENCH_KV_ROWS" $kv >>"$raw" 2>&1 ||
	{ tail -20 "$raw"; exit 1; }
ydb workload stock init --min-partitions "$BENCH_PARTITIONS" -p "$BENCH_PRODUCTS" -q 1000000 -o "$BENCH_ORDERS" >>"$raw" 2>&1 ||
	{ tail -20 "$raw"; exit 1; }

q0=$(node_queries)
rows=""
table=$(printf '%-24s %7s %9s %9s %7s %7s %7s %7s %7s %7s' workload threads txs "txs/s" retries errors "p50 ms" "p95 ms" "p99 ms" "max ms")
for w in "kv upsert" "kv select" "stock put-rand-order" "stock rand-user-hist"; do
	set -- $w
	extra=""
	[ "$1" = kv ] && extra=$kv
	for t in $BENCH_THREADS; do
		echo "run: $w, $t threads, ${BENCH_TIME}s"
		out=results/.run.$$
		ydb workload "$1" run "$2" --seconds "$BENCH_TIME" --threads "$t" --quiet $extra >"$out" 2>&1 ||
			{ cat "$out" >>"$raw"; tail -20 "$out"; rm -f "$out"; exit 1; }
		{ echo "=== $w threads=$t"; cat "$out"; } >>"$raw"
		# the summary is a "Total  Txs  Txs/Sec  Retries  Errors  p50(ms) ..." header plus one row;
		# output line 1: JSON object, line 2: table row
		parsed=$(awk -v w="$1 $2" -v t="$t" '
			$1 == "Total" && $2 == "Txs" { getline; s = $1; txs = $2; tps = $3; re = $4; er = $5; p50 = $6; p95 = $7; p99 = $8; pmax = $9; found = 1 }
			END {
				if (!found) exit 1
				printf "{\"workload\":\"%s\",\"threads\":%d,\"seconds\":%s,\"txs\":%s,\"txs_per_sec\":%s,\"retries\":%s,\"errors\":%s,\"p50_ms\":%s,\"p95_ms\":%s,\"p99_ms\":%s,\"max_ms\":%s}\n", w, t, s, txs, tps, re, er, p50, p95, p99, pmax
				printf "%-24s %7s %9s %9s %7s %7s %7s %7s %7s %7s\n", w, t, txs, tps, re, er, p50, p95, p99, pmax
			}' "$out") || { echo "no summary in CLI output:"; cat "$out"; rm -f "$out"; exit 1; }
		rm -f "$out"
		rows="${rows:+$rows,}$(echo "$parsed" | sed -n 1p)"
		table="$table
$(echo "$parsed" | sed -n 2p)"
	done
done

# one scrape interval, so Prometheus has the counters as of the last run
sleep 16
q1=$(node_queries)
# "node delta" per line, then JSON object and a one-line summary with shares
per_node=$(printf '%s\n%s\n' "$q0" "$q1" | awk 'NF == 2 { if ($1 in a) d[$1] = $2 - a[$1]; else a[$1] = $2 }
	END { for (n in d) { tot += d[n] }; for (n in d) printf "%s %d %.0f\n", n, d[n], tot ? 100 * d[n] / tot : 0 }' | sort)
node_json=$(echo "$per_node" | awk 'NF == 3 { printf "%s\"%s\": %d", (n++ ? ", " : ""), $1, $2 }')
node_line=$(echo "$per_node" | awk 'NF == 3 { printf "%s%s %d (%d%%)", (n++ ? ", " : ""), $1, $2, $3 }')

cli_version=$(docker exec ydb-storage-1 /ydb version 2>/dev/null | sed 's/^YDB CLI //')
image=$(docker inspect --format '{{.Config.Image}}' ydb-storage-1)
cpus=$(docker info --format '{{.NCPU}}')
mem=$(docker info --format '{{.MemTotal}}')
arch=$(docker info --format '{{.Architecture}}')

cat >"$json" <<JSON
{
  "timestamp": "$stamp",
  "system": "ydb-docker-compose-cluster",
  "versions": {"ydb_cli": "$cli_version", "image": "$image"},
  "topology": {"storage_nodes": 3, "dynamic_nodes": 2, "erasure": "mirror-3-dc", "database": "/Root/testdb",
               "endpoints": "$endpoints", "queries_per_dynamic_node": {$node_json}},
  "parameters": {"time_s": $BENCH_TIME, "threads": "$BENCH_THREADS", "kv_rows": $BENCH_KV_ROWS,
                 "kv_max_first_key": $KV_KEYS, "stock_products": $BENCH_PRODUCTS,
                 "stock_orders": $BENCH_ORDERS, "min_partitions": $BENCH_PARTITIONS},
  "machine": {"docker_cpus": $cpus, "docker_mem_bytes": $mem, "docker_arch": "$arch",
              "host": "$(uname -sm)", "ydb_emulated": $([ "$arch" = x86_64 ] && echo false || echo true)},
  "results": [$rows]
}
JSON

echo
echo "YDB CLI $cli_version -> /Root/testdb via $endpoints ($image)"
echo "${BENCH_TIME}s per run, Docker VM: $cpus CPUs, $((mem / 1073741824)) GiB, $arch"
echo "$table"
echo "queries per dynamic node (Prometheus kqp_Requests_QueryExecute): ${node_line:-n/a}"
echo
echo "raw: $raw  json: $json"
