-- One table, one join-free aggregate MV, an update, and a check that the MV equals the batch query.
CREATE TABLE readings (sensor_id INT PRIMARY KEY, site VARCHAR, value BIGINT);
CREATE MATERIALIZED VIEW site_totals AS
SELECT site, count(*) AS sensors, sum(value) AS total FROM readings GROUP BY site;

INSERT INTO readings SELECT g, 'site-' || (g % 4), g FROM generate_series(1, 100000) g;
FLUSH;
SELECT * FROM site_totals ORDER BY site;

UPDATE readings SET site = 'site-x' WHERE sensor_id <= 1000;
FLUSH;
SELECT * FROM site_totals ORDER BY site;

-- division by zero unless the MV equals the batch query
SELECT 1 / (CASE WHEN (SELECT sum(total) FROM site_totals) = 5000050000
                  AND (SELECT total FROM site_totals WHERE site = 'site-x') = 500500 THEN 1 ELSE 0 END) AS mv_ok;

SELECT w.host, w.type, count(a.actor_id) AS actors
FROM rw_worker_nodes w LEFT JOIN rw_actors a ON a.worker_id = w.id
WHERE w.type = 'WORKER_TYPE_COMPUTE_NODE' GROUP BY w.host, w.type ORDER BY w.host;
DROP MATERIALIZED VIEW site_totals;
DROP TABLE readings;
