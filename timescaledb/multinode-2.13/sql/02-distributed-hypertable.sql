-- A distributed hypertable: partitioned by time (1-day chunks) AND by device_id (3 space
-- partitions, one per data node); every chunk is stored on 2 data nodes (replication_factor 2).
\timing on
SET client_min_messages = warning;
DROP TABLE IF EXISTS conditions;
CREATE TABLE conditions (
  time        timestamptz NOT NULL,
  device_id   integer NOT NULL,
  temperature double precision
);
SELECT * FROM create_distributed_hypertable('conditions', 'time', 'device_id',
  chunk_time_interval => interval '1 day', replication_factor => 2);

-- 300 devices x 3 days x one reading per minute = 1.3M rows, written through the access node
INSERT INTO conditions
SELECT t, d, round((20 + 5 * sin(extract(epoch FROM t) / 86400 * 2 * pi()) + random())::numeric, 2)
FROM generate_series(date_trunc('day', now()) - interval '3 days', date_trunc('day', now()) - interval '1 minute', interval '1 minute') AS t,
     generate_series(1, 300) AS d;

-- 3 days x 3 space partitions = 9 chunks, each on 2 of the 3 data nodes
SELECT chunk_name, range_start::date AS day, data_nodes
FROM timescaledb_information.chunks WHERE hypertable_name = 'conditions'
ORDER BY range_start, chunk_name;

-- rows per data node (each row is on two of them) and size per node
SELECT node_name, pg_size_pretty(total_bytes) AS size
FROM hypertable_detailed_size('conditions') WHERE node_name IS NOT NULL ORDER BY 1;
SELECT count(*) AS rows_seen_by_access_node FROM conditions;

-- the access node pushes the aggregation down: each data node computes partial (or full)
-- aggregates for the chunks it is "responsible" for, and only grouped rows come back
EXPLAIN (VERBOSE, COSTS OFF)
SELECT device_id, max(temperature) FROM conditions
WHERE time > now() - interval '2 days' GROUP BY device_id;

SELECT time_bucket('1 day', time)::date AS day, count(*) AS readings, round(avg(temperature)::numeric, 3) AS avg_temp
FROM conditions GROUP BY 1 ORDER BY 1;
