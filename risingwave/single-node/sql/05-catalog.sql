-- What is running: streaming jobs, their parallelism, and the workers.
SHOW MATERIALIZED VIEWS;
SHOW SINKS;
SELECT name, relation_type, parallelism FROM rw_streaming_parallelism ORDER BY name;
SELECT id, type, state, parallelism, system_total_cpu_cores AS cpus FROM rw_worker_nodes ORDER BY id;
SELECT version();
