-- Columnstore (formerly "compression"): chunks older than 3 days are rewritten column by column,
-- one batch of up to 1000 rows per sensor_id (segmentby), ordered by time (orderby).
\timing on

-- convert the old chunks now (a columnstore policy would do the same in the background)
DO $$
DECLARE c regclass;
BEGIN
  FOR c IN SELECT show_chunks('conditions', older_than => now() - interval '3 days') LOOP
    CALL convert_to_columnstore(c, if_not_columnstore => true);
  END LOOP;
END $$;

-- before/after size of the converted chunks and the ratio
SELECT total_chunks, number_compressed_chunks AS columnstore_chunks,
       pg_size_pretty(before_compression_total_bytes) AS before,
       pg_size_pretty(after_compression_total_bytes) AS after,
       round(before_compression_total_bytes::numeric / after_compression_total_bytes, 1) AS ratio
FROM hypertable_columnstore_stats('conditions');
SELECT pg_size_pretty(hypertable_size('conditions')) AS hypertable_size_now;

-- recent chunks stay in the rowstore for fast inserts/updates
SELECT is_compressed AS columnstore, count(*) AS chunks
FROM timescaledb_information.chunks WHERE hypertable_name = 'conditions' GROUP BY 1;

-- columnstore chunks are read by ColumnarScan (only the columns the query needs, vectorized)
EXPLAIN (COSTS OFF)
SELECT sensor_id, avg(temperature) FROM conditions
WHERE time < now() - interval '20 days' AND time > now() - interval '21 days' GROUP BY sensor_id;

-- still writable: insert into and update a columnstore chunk
INSERT INTO conditions VALUES (now() - interval '10 days', 1, -40, 0);
UPDATE conditions SET humidity = 1 WHERE sensor_id = 1 AND temperature = -40;
SELECT time::date AS day, sensor_id, temperature, humidity FROM conditions WHERE temperature = -40;
DELETE FROM conditions WHERE temperature = -40;

-- CREATE TABLE ... WITH (tsdb.hypertable) already added a columnstore policy (a background job);
-- replace it with one that converts chunks once they are 3 days old
SELECT job_id, proc_name, config::text FROM timescaledb_information.jobs
WHERE hypertable_name = 'conditions' AND proc_name = 'policy_compression';
CALL remove_columnstore_policy('conditions');
CALL add_columnstore_policy('conditions', after => interval '3 days');
SELECT job_id, proc_name, config::text FROM timescaledb_information.jobs
WHERE hypertable_name = 'conditions' AND proc_name = 'policy_compression';
