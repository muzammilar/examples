-- A hypertable: a normal Postgres table that TimescaleDB splits into time chunks.
-- 200 sensors x 30 days x one reading per 5 minutes = 1.7M rows, ending now().
\timing on
SET client_min_messages = warning; -- hide DROP ... IF EXISTS notices
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit; -- hyperfunctions (04-gapfill-hyperfunctions.sql)
DROP MATERIALIZED VIEW IF EXISTS conditions_daily, conditions_hourly CASCADE;
DROP TABLE IF EXISTS conditions, sensors CASCADE;
SELECT extversion AS timescaledb, (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb_toolkit') AS toolkit
FROM pg_extension WHERE extname = 'timescaledb';

-- relational metadata: a plain Postgres table
CREATE TABLE sensors (
  id       integer PRIMARY KEY,
  site     text NOT NULL,
  kind     text NOT NULL
);
INSERT INTO sensors
SELECT i, 'site-' || (1 + i % 10), (ARRAY['indoor','outdoor','freezer'])[1 + i % 3]
FROM generate_series(1, 200) AS g(i);

-- the time-series: CREATE TABLE ... WITH (tsdb.hypertable) makes it a hypertable in one
-- statement, partitioned by "time" into 1-day chunks; segmentby/orderby set up the columnstore
-- (used in 02-columnstore.sql)
CREATE TABLE conditions (
  time        timestamptz NOT NULL,
  sensor_id   integer NOT NULL REFERENCES sensors,
  temperature double precision,
  humidity    double precision
) WITH (
  tsdb.hypertable,
  tsdb.partition_column = 'time',
  tsdb.chunk_interval = '1 day',
  tsdb.segmentby = 'sensor_id',
  tsdb.orderby = 'time DESC'
);

INSERT INTO conditions
SELECT t, s,
       round((20 + 8 * sin(extract(epoch FROM t) / 86400 * 2 * pi()) + s % 7 + random())::numeric, 2), -- daily cycle
       round((50 + 20 * random())::numeric, 1)
FROM generate_series(date_trunc('hour', now()) - interval '30 days', now(), interval '5 minutes') AS t,
     generate_series(1, 200) AS s;
ANALYZE conditions;

-- every chunk is a child table covering one day
SELECT count(*) AS chunks, min(range_start)::date AS first_day, max(range_end)::date AS last_day
FROM timescaledb_information.chunks WHERE hypertable_name = 'conditions';
SELECT chunk_name, range_start, range_end
FROM timescaledb_information.chunks WHERE hypertable_name = 'conditions'
ORDER BY range_start DESC LIMIT 3;
SELECT pg_size_pretty(hypertable_size('conditions')) AS hypertable_size, count(*) AS rows FROM conditions;

-- chunk exclusion: a query for the last 2 days only touches the last 2-3 chunks
EXPLAIN (COSTS OFF)
SELECT sensor_id, max(temperature) FROM conditions
WHERE time > now() - interval '2 days' GROUP BY sensor_id;

-- it is still Postgres: join with the metadata table in plain SQL
SELECT s.site, s.kind, round(avg(c.temperature)::numeric, 2) AS avg_temp, count(*) AS readings
FROM conditions c JOIN sensors s ON s.id = c.sensor_id
WHERE c.time > now() - interval '1 day'
GROUP BY s.site, s.kind ORDER BY s.site, s.kind LIMIT 6;
