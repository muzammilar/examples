-- Where the streaming actors run: each fragment is split into actors spread over both compute
-- nodes (2 x parallelism 2 here).
SELECT id, host, type, state, parallelism FROM rw_worker_nodes ORDER BY id;
SELECT w.host AS compute_node, count(*) AS actors
FROM rw_actors a JOIN rw_worker_nodes w ON a.worker_id = w.id
GROUP BY w.host ORDER BY w.host;
SELECT name, relation_type, parallelism FROM rw_streaming_parallelism ORDER BY name;
