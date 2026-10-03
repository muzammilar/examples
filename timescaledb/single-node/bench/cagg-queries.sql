-- `make benchmark`: the two whole-range queries of bench/queries.sql answered from the hourly
-- continuous aggregate bench_hourly instead of the raw rows.
\timing on
\pset pager off
\o /dev/null

\echo query: hourly-avg-all
SELECT hour, sum(sum_temp) / sum(n), max(max_hum)
FROM bench_hourly GROUP BY hour ORDER BY hour;

\echo query: daily-max-per-device
SELECT time_bucket('1 day', hour) AS day, device_id, max(max_temp)
FROM bench_hourly GROUP BY day, device_id;
\o
