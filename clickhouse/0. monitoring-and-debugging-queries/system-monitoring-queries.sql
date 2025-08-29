/* Get free disk space */
SELECT *
FROM system.disks
FORMAT JSON;

/* Get free disk space - human readable */
SELECT
    name,
    path,
    formatReadableSize(free_space),
    formatReadableSize(total_space),
    formatReadableSize(keep_free_space)
FROM system.disks

/* Get Query Metrics */
SELECT *
FROM system.metrics
FORMAT JSON;

SELECT *
FROM system.asynchronous_metrics
FORMAT JSON;

/* Get All clusters information */
SELECT *
FROM system.clusters
FORMAT JSON;

/* Get System utilization */
SELECT *
FROM system.events
FORMAT JSON;

/* Metric Log */
SELECT *
FROM system.metric_log
LIMIT 1
FORMAT JSON

/* Server benchmarking - Look at `top` while running these*/
/* https://clickhouse.tech/docs/en/operations/system_tables/ */

/* Sequential Reads */
SELECT *
FROM system.numbers;

/* Parallel Reads */
SELECT *
FROM system.numbers_mt;


/* Get all parts of all tables*/
SELECT *
FROM system.parts;

/* Get all the settings */
SELECT *
FROM system.settings;

/* Get all the settings that have changed: https://clickhouse.tech/docs/en/operations/system_tables/#system-settings */
SELECT *
FROM system.settings
WHERE changed;

/* ZooKeeper information (if configured): https://clickhouse.tech/docs/en/operations/system_tables/#system-zookeeper */
SELECT *
FROM system.zookeeper;

/* Replica Information for distributed tables */
SELECT *
FROM system.replicas;

/* Validate if the data in table is correct or if the table is corrupted */
CHECK TABLE my_database.my_table_local;
