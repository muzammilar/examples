#!/bin/sh
# Prints the TPC-H benchmark as a duckdb CLI script (run by `make benchmark`):
#   sh bench/tpch.sh SF TIMESTAMP DOCKER_CPUS DOCKER_MEM_BYTES LIMIT_CPUS LIMIT_MEM | duckdb
# Every timed statement writes its JSON profile to /data/bench/prof/<phase>-<query>-<run>.json
# (the pragma after it points the profiler at a scratch file); the summary at the end
# reads those files back with read_json.
set -eu
sf=$1 ts=$2 cpus=$3 mem=$4 limit_cpus=$5 limit_mem=$6
runs=3
pq_queries="1 3 6 9 13 18" # re-run on Parquet: scan-heavy, join-heavy and aggregation-heavy
tables="customer lineitem nation orders part partsupp region supplier"
out=/data/bench
timed() { # timed <profile name> <statement>
	echo "PRAGMA profiling_output = '$out/prof/$1.json';"
	echo "$2"
	echo "PRAGMA profiling_output = '$out/prof/_.json';"
}

cat <<EOF
.bail on
.output /dev/null
-- the tpch extension is downloaded from extensions.duckdb.org on first use, kept in ./data
SET extension_directory = '/data/extensions';
INSTALL tpch;
LOAD tpch;
ATTACH '$out/tpch.duckdb' AS tpch;
USE tpch;
PRAGMA enable_profiling = 'json';
PRAGMA profiling_output = '$out/prof/_.json';
EOF
timed dbgen-0-1 "CALL dbgen(sf = $sf);"
echo "CHECKPOINT;"
for r in $(seq $runs); do
	for q in $(seq 22); do timed "duckdb-$q-$r" "PRAGMA tpch($q);"; done
done

timed export-0-1 "EXPORT DATABASE '$out/parquet' (FORMAT parquet);"
echo "ATTACH ':memory:' AS pq;"
echo "USE pq;"
for t in $tables; do echo "CREATE VIEW $t AS FROM '$out/parquet/$t.parquet';"; done
for r in $(seq $runs); do
	for q in $pq_queries; do timed "parquet-$q-$r" "PRAGMA tpch($q);"; done
done

cat <<EOF
PRAGMA disable_profiling;
CREATE TEMP TABLE prof AS
SELECT k.phase, k.q::INT AS q, k.run::INT AS run, latency
FROM (SELECT regexp_extract(filename, '/(\w+)-(\d+)-(\d+)\.json$', ['phase', 'q', 'run']) AS k,
             latency
      FROM read_json('$out/prof/*-*-*.json', filename = true,
                     columns = {latency: 'DOUBLE'}));
CREATE TEMP TABLE per_query AS
SELECT q,
       median(latency) FILTER (phase = 'duckdb') AS duckdb_median_s,
       min(latency) FILTER (phase = 'duckdb') AS duckdb_min_s,
       median(latency) FILTER (phase = 'parquet') AS parquet_median_s
FROM prof WHERE phase IN ('duckdb', 'parquet') GROUP BY q;
CREATE TEMP TABLE summary AS
SELECT (SELECT latency FROM prof WHERE phase = 'dbgen') AS dbgen_s,
       (SELECT latency FROM prof WHERE phase = 'export') AS parquet_export_s,
       sum(duckdb_median_s) AS duckdb_22_total_s,
       exp(avg(ln(duckdb_median_s))) AS duckdb_geomean_s,
       sum(duckdb_median_s) FILTER (parquet_median_s IS NOT NULL) AS duckdb_subset_total_s,
       sum(parquet_median_s) AS parquet_subset_total_s,
       (SELECT sum(size) FROM read_blob('$out/parquet/*.parquet')) AS parquet_bytes,
       (SELECT sum(size) FROM read_blob('$out/tpch.duckdb')) AS db_file_bytes
FROM per_query;
.output
.mode box
.nullvalue ''
SELECT 'Q' || lpad(q::VARCHAR, 2, '0') AS query,
       round(duckdb_median_s * 1000, 1) AS "duckdb ms (median of $runs)",
       round(duckdb_min_s * 1000, 1) AS "min ms",
       round(parquet_median_s * 1000, 1) AS "parquet ms (median)"
FROM per_query ORDER BY q;
UNPIVOT (
  SELECT version() AS duckdb, '$sf' AS "scale factor", current_setting('threads')::VARCHAR AS threads,
         round(dbgen_s, 2) || ' s' AS dbgen,
         round(duckdb_22_total_s, 2) || ' s' AS "22 queries (sum of medians)",
         round(duckdb_geomean_s * 1000, 1) || ' ms' AS "22 queries (geometric mean)",
         round(parquet_export_s, 2) || ' s' AS "EXPORT DATABASE to Parquet",
         round(duckdb_subset_total_s, 2) || ' s / ' || round(parquet_subset_total_s, 2) || ' s'
           AS "Q$(echo $pq_queries | sed 's/ /,/g'): native / Parquet",
         format_bytes(db_file_bytes::BIGINT) || ' / ' || format_bytes(parquet_bytes::BIGINT)
           AS "size: native / Parquet"
  FROM summary
) ON COLUMNS(*) INTO NAME metric VALUE value;
.output /dev/null
COPY (
  SELECT 'duckdb' AS db, version() AS db_version,
         '$ts' AS timestamp,
         {sf: $sf, runs: $runs, parquet_queries: [$(echo $pq_queries | tr ' ' ,)],
          threads: current_setting('threads'), memory_limit: current_setting('memory_limit')} AS params,
         {docker_vm_cpus: $cpus, docker_vm_mem_gb: round($mem / 1024 ^ 3, 1),
          platform: (SELECT platform FROM pragma_platform())} AS machine,
         {cpus: $limit_cpus, memory: '$limit_mem'} AS limits,
         {summary: (SELECT summary FROM summary),
          queries: (SELECT list(per_query ORDER BY q) FROM per_query)} AS results
) TO '/results/duckdb-$ts.json' (FORMAT json);
.output
SELECT 'wrote results/duckdb-$ts.json' AS "==>";
EOF
