-- Through HAProxy :5000, i.e. on whichever node is the primary right now.
\timing on
SET client_min_messages = warning;
SELECT inet_server_addr() AS primary_ip, pg_is_in_recovery() AS in_recovery,
       (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb') AS timescaledb;

-- both replicas stream WAL from the primary, each through its own replication slot
SELECT application_name AS replica, state, sync_state, replay_lag
FROM pg_stat_replication ORDER BY 1;
SELECT slot_name, active FROM pg_replication_slots ORDER BY 1;

DROP MATERIALIZED VIEW IF EXISTS conditions_hourly CASCADE;
DROP TABLE IF EXISTS conditions;
CREATE TABLE conditions (
  time        timestamptz NOT NULL,
  sensor_id   integer NOT NULL,
  temperature double precision
) WITH (tsdb.hypertable, tsdb.partition_column = 'time', tsdb.chunk_interval = '1 day',
        tsdb.segmentby = 'sensor_id', tsdb.orderby = 'time DESC');

-- 100 sensors x 7 days x one reading per minute = 1M rows
INSERT INTO conditions
SELECT t, s, round((20 + 8 * sin(extract(epoch FROM t) / 86400 * 2 * pi()) + random())::numeric, 2)
FROM generate_series(date_trunc('minute', now()) - interval '7 days', now(), interval '1 minute') AS t,
     generate_series(1, 100) AS s;

-- columnstore and a continuous aggregate, created on the primary; they replicate like any table
DO $$
DECLARE c regclass;
BEGIN
  FOR c IN SELECT show_chunks('conditions', older_than => now() - interval '1 day') LOOP
    CALL convert_to_columnstore(c, if_not_columnstore => true);
  END LOOP;
END $$;
CREATE MATERIALIZED VIEW conditions_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 hour', time) AS bucket, sensor_id, avg(temperature) AS avg_temp, count(*) AS n
FROM conditions GROUP BY bucket, sensor_id;

SELECT count(*) AS rows, pg_size_pretty(hypertable_size('conditions')) AS size,
       (SELECT count(*) FROM timescaledb_information.chunks
        WHERE hypertable_name = 'conditions' AND is_compressed) AS columnstore_chunks
FROM conditions;

-- the WAL position the replicas have to reach
SELECT pg_current_wal_lsn() AS primary_lsn;
SELECT application_name AS replica, replay_lsn, pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS bytes_behind
FROM pg_stat_replication ORDER BY 1;
