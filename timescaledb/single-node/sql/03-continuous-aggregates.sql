-- Continuous aggregates: materialized views that refresh incrementally.
-- materialized_only = false (real-time aggregation) adds rows not yet materialized at query time.
\timing on

CREATE MATERIALIZED VIEW conditions_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 hour', time) AS bucket, sensor_id,
       avg(temperature) AS avg_temp, min(temperature) AS min_temp, max(temperature) AS max_temp,
       count(*) AS readings
FROM conditions
GROUP BY bucket, sensor_id
WITH NO DATA;

-- materialize everything up to 1 hour ago; the last hour comes from the raw table at query time
CALL refresh_continuous_aggregate('conditions_hourly', NULL, now() - interval '1 hour');
-- keep it refreshed every 30 minutes in the background
SELECT add_continuous_aggregate_policy('conditions_hourly',
  start_offset => interval '3 days', end_offset => interval '1 hour',
  schedule_interval => interval '30 minutes') > 0 AS policy_added;

-- hierarchical: a daily aggregate on top of the hourly one
CREATE MATERIALIZED VIEW conditions_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 day', bucket) AS day, sensor_id,
       sum(avg_temp * readings) / sum(readings) AS avg_temp, max(max_temp) AS max_temp, sum(readings) AS readings
FROM conditions_hourly
GROUP BY day, sensor_id
WITH NO DATA;
CALL refresh_continuous_aggregate('conditions_daily', NULL, now() - interval '1 day');

-- same answer, raw table vs hourly aggregate: 30 days of hourly averages for one site
\echo 'raw table:'
SELECT count(*) AS hours, round(avg(avg_temp)::numeric, 3) AS avg_of_hourly_avgs FROM (
  SELECT time_bucket('1 hour', time) AS bucket, avg(temperature) AS avg_temp
  FROM conditions c JOIN sensors s ON s.id = c.sensor_id WHERE s.site = 'site-1'
  GROUP BY bucket, sensor_id) x;
\echo 'continuous aggregate:'
SELECT count(*) AS hours, round(avg(avg_temp)::numeric, 3) AS avg_of_hourly_avgs
FROM conditions_hourly h JOIN sensors s ON s.id = h.sensor_id WHERE s.site = 'site-1';

-- real-time aggregation: a fresh reading shows up in the aggregate before any refresh
INSERT INTO conditions VALUES (now(), 1, 99, 50);
SELECT bucket, sensor_id, max_temp, readings FROM conditions_hourly
WHERE sensor_id = 1 ORDER BY bucket DESC LIMIT 2;
DELETE FROM conditions WHERE temperature = 99;

SELECT day::date, round(avg_temp::numeric, 2) AS avg_temp, round(max_temp::numeric, 2) AS max_temp, readings
FROM conditions_daily WHERE sensor_id = 1 ORDER BY day DESC LIMIT 3;

SELECT view_name, materialized_only, compression_enabled AS columnstore
FROM timescaledb_information.continuous_aggregates ORDER BY 1;
