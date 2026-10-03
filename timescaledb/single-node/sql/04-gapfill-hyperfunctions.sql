-- time_bucket / time_bucket_gapfill and hyperfunctions (timescaledb_toolkit).
\timing on

-- time_bucket: 15-minute buckets
SELECT time_bucket('15 minutes', time) AS bucket, round(avg(temperature)::numeric, 2) AS avg_temp
FROM conditions WHERE sensor_id = 1 AND time > now() - interval '1 hour'
GROUP BY bucket ORDER BY bucket;

-- punch a hole: drop sensor 2's readings from 6 to 3 hours ago
DELETE FROM conditions WHERE sensor_id = 2 AND time BETWEEN now() - interval '6 hours' AND now() - interval '3 hours';

-- time_bucket_gapfill emits the empty buckets; locf() carries the last value forward,
-- interpolate() draws a line between the neighbours
SELECT time_bucket_gapfill('1 hour', time) AS hour,
       round(avg(temperature)::numeric, 2) AS avg_temp,
       locf(round(avg(temperature)::numeric, 2)) AS locf,
       interpolate(round(avg(temperature)::numeric, 2)) AS interpolated
FROM conditions
WHERE sensor_id = 2 AND time > now() - interval '8 hours' AND time < now()
GROUP BY hour ORDER BY hour;

-- first()/last(): the latest reading per sensor
SELECT sensor_id, last(temperature, time) AS last_temp, max(time) AS at
FROM conditions WHERE time > now() - interval '1 hour' AND sensor_id <= 3
GROUP BY sensor_id ORDER BY sensor_id;

-- toolkit: approximate percentiles (percentile_agg), statistics (stats_agg),
-- and the time-weighted average, which accounts for the 3-hour gap of sensor 2
SELECT sensor_id,
       round(approx_percentile(0.5, percentile_agg(temperature))::numeric, 2) AS p50,
       round(approx_percentile(0.99, percentile_agg(temperature))::numeric, 2) AS p99,
       round(stddev(stats_agg(temperature))::numeric, 3) AS stddev,
       round(avg(temperature)::numeric, 3) AS plain_avg,
       round(average(time_weight('Linear', time, temperature))::numeric, 3) AS time_weighted_avg
FROM conditions
WHERE sensor_id IN (1, 2) AND time > now() - interval '8 hours'
GROUP BY sensor_id ORDER BY sensor_id;

-- candlestick (open/high/low/close) per day for one sensor
SELECT time_bucket('1 day', time)::date AS day,
       round(open(c)::numeric, 2) AS open, round(high(c)::numeric, 2) AS high,
       round(low(c)::numeric, 2) AS low, round(close(c)::numeric, 2) AS close
FROM (SELECT time_bucket('1 day', time) AS time, candlestick_agg(time, temperature, 1) AS c
      FROM conditions WHERE sensor_id = 1 AND time > now() - interval '3 days' GROUP BY 1) x
ORDER BY day;
