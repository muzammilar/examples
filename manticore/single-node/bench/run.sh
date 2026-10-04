#!/bin/sh
# make benchmark: manticore-load (ships with the Manticore image) in its own container against
# the `manticore` service. Writes results/<NAME>.txt (and prints it).
#   1. ingest DOCS documents (random English text 10-40 words + 5 attributes + 64-dim vector)
#      into an RT columnar table with an HNSW index, THREADS writers, batches of BATCH
#   2. queries, QUERIES each, THREADS clients: full-text (1 and 2 random words), full-text +
#      filter + facet-style GROUP BY, filter-only (secondary index), KNN top 10
set -eu
# bench/words.txt: the 365 words manticore-load's <text/...> generator uses (extracted from its
# output), so every query word occurs in the data
H=${MANTICORE_HOST:-manticore}
DOCS=${DOCS:-500000}
THREADS=${THREADS:-8}
BATCH=${BATCH:-5000}
QUERIES=${QUERIES:-20000}
KNN_QUERIES=${KNN_QUERIES:-5000}
out=/results/${NAME:-bench}.txt
SQL="mysql -h$H -P9306 --skip-table -N" # the image's my.cnf sets `table`

run() { # label, manticore-load args...; one line: ops/s (docs/s for inserts) and latency per request
	label=$1; shift
	manticore-load --host="$H" --quiet --json --latency-histograms=0 "$@" 2>&1 | awk -v l="$label" -F": " '
		/"total_operations"/ { gsub(/,/, "", $2); n = $2 }
		/"operations_per_second"/ { gsub(/,/, "", $2); r = $2 }
		/"avg"/ && lat { gsub(/,/, "", $2); a = $2 } /"latency"/ { lat = 1 }
		/"p50"/ && lat { gsub(/,/, "", $2); p50 = $2 } /"p95"/ && lat { gsub(/,/, "", $2); p95 = $2 }
		/"p99"/ && lat { gsub(/,/, "", $2); p99 = $2 }
		/rror|ailed/ { err = err $0 " " }
		END { if (err != "" || r == "") { print l " | FAILED " err; exit 1 }
			printf "%-40s | %9s | %9s | %7s | %7s | %7s | %7s\n", l, n, r, a, p50, p95, p99 }' | tee -a "$out"
}

{
	echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
	echo "host: ${HOST_INFO:-?}; ${DOCKER_INFO:-?}"
	echo "limits: ${BENCH_LIMITS:-none}"
	echo "server: $($SQL -e "SHOW VERSION" | head -n 1 | tr "\t" " ")"
	echo "docs=$DOCS threads=$THREADS batch=$BATCH queries=$QUERIES knn_queries=$KNN_QUERIES"
	echo
} | tee "$out"

printf "%-40s | %9s | %9s | %7s | %7s | %7s | %7s\n" phase total "ops/s" "avg ms" p50 p95 p99 | tee -a "$out"
run "ingest" --drop --batch-size="$BATCH" --threads="$THREADS" --total="$DOCS" \
	--init="CREATE TABLE bench (message text, service string, status int, latency_ms int, bytes bigint, ts timestamp, v float_vector knn_type='hnsw' knn_dims='64' hnsw_similarity='l2') engine='columnar'" \
	--load="INSERT INTO bench (id,message,service,status,latency_ms,bytes,ts,v) VALUES (<increment>,'<text/10/40>','svc<int/1/50>',<int/200/504>,<int/1/3000>,<int/100/100000>,<int/1780000000/1790000000>,(<array_float/64/64/0/1>))"
# wait for background merges of disk chunks so queries see the steady state
i=0
until [ "$($SQL -e "SHOW TABLE bench STATUS LIKE 'optimizing'" | cut -f2)" = 0 ] || [ $i -ge 900 ]; do i=$((i + 1)); sleep 1; done
for k in indexed_documents disk_bytes ram_bytes disk_chunks; do $SQL -e "SHOW TABLE bench STATUS LIKE '$k'"; done | tee -a "$out"

run "match 1 word, top 20" --threads="$THREADS" --total="$QUERIES" \
	--load="SELECT id FROM bench WHERE MATCH('<text/{/bench/words.txt}/1/1>') LIMIT 20"
run "match 2 words, top 20" --threads="$THREADS" --total="$QUERIES" \
	--load="SELECT id FROM bench WHERE MATCH('<text/{/bench/words.txt}/2/2>') LIMIT 20"
run "match + status>=500 + group by service" --threads="$THREADS" --total="$QUERIES" \
	--load="SELECT service, COUNT(*) c FROM bench WHERE MATCH('<text/{/bench/words.txt}/1/1>') AND status>=500 GROUP BY service ORDER BY c DESC LIMIT 10"
run "filter only: status=X and latency<100" --threads="$THREADS" --total="$QUERIES" \
	--load="SELECT id FROM bench WHERE status=<int/200/504> AND latency_ms<100 LIMIT 20"
run "KNN top 10, 64 dims" --threads="$THREADS" --total="$KNN_QUERIES" \
	--load="SELECT id, KNN_DIST() FROM bench WHERE KNN(v, 10, (<array_float/64/64/0/1>))"
[ -n "${KEEP:-}" ] || $SQL -e "DROP TABLE bench"
echo "written: $out"
