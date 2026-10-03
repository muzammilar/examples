-- `make benchmark`: queries over bench_readings, the same on every layout; bench/report.py
-- takes the "Time:" line after each "query: <name>".
\timing on
\pset pager off
\o /dev/null
SET client_min_messages = error;

\echo query: max-per-device
SELECT device_id, max(temperature) FROM bench_readings GROUP BY device_id;

\echo query: hourly-avg-all
SELECT time_bucket('1 hour', time) AS hour, avg(temperature) FROM bench_readings GROUP BY hour ORDER BY hour;

\echo query: one-device-1day
SELECT time_bucket('5 minutes', time) AS t, avg(temperature) FROM bench_readings
WHERE device_id = 42 AND time >= timestamptz '2026-01-01' + interval '1 day' AND time < timestamptz '2026-01-01' + interval '2 days'
GROUP BY t ORDER BY t;

\echo query: count-threshold
SELECT count(*) FROM bench_readings WHERE temperature > 29;
\o
