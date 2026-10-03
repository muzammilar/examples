-- `make benchmark`: dashboard-style queries over bench_readings (DEVICES x DAYS x 1/minute).
-- Run RUNS times on the rowstore, again after convert_to_columnstore; bench/report.py takes the
-- "Time:" line after each "query: <name>".
\timing on
\pset pager off
\o /dev/null

\echo query: hourly-avg-all
SELECT time_bucket('1 hour', time) AS hour, avg(temperature), max(humidity)
FROM bench_readings GROUP BY hour ORDER BY hour;

\echo query: daily-max-per-device
SELECT time_bucket('1 day', time) AS day, device_id, max(temperature)
FROM bench_readings GROUP BY day, device_id;

\echo query: one-device-1day
SELECT time_bucket('5 minutes', time) AS t, avg(temperature)
FROM bench_readings
WHERE device_id = 42 AND time > (SELECT max(time) FROM bench_readings) - interval '1 day'
GROUP BY t ORDER BY t;

\echo query: lastpoint
SELECT DISTINCT ON (device_id) device_id, time, temperature
FROM bench_readings
WHERE time > (SELECT max(time) FROM bench_readings) - interval '1 hour'
ORDER BY device_id, time DESC;

\echo query: threshold-count
SELECT device_id, count(*) FROM bench_readings WHERE temperature > 29 GROUP BY device_id;
\o
