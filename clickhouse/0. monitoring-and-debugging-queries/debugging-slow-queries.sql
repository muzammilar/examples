/* Top 10 slow queries on the server */
SELECT
    query_id,
    query,
    query_start_time,
    query_duration_ms,
    length(columns) as num_columns,
    length(partitions) as num_partitions,
    length(tables) as num_tables,
    peak_threads_usage,
    used_storages,
    read_rows,
    read_bytes
FROM
    system.query_log
WHERE
    type = 'QueryFinish'
ORDER BY
    query_duration_ms DESC
LIMIT 10;

/* Explain Query - May require write access */
EXPLAIN SELECT my_col FROM my_db.my_table LIMIT 10;

/* Top 5 Slow Queries in the last 10 hours on all nodes in the super_cool_replicated_cluster */
SELECT
    type,
    event_time,
    query_duration_ms,
    query,
    read_rows,
    length(columns) as num_columns,
    length(partitions) as num_partitions,
    length(tables) as num_tables,
    peak_threads_usage,
    tables
FROM clusterAllReplicas(super_cool_replicated_cluster, system.query_log)
WHERE (event_time >= (now() - toIntervalMinute(600))) AND type='QueryFinish'
ORDER BY query_duration_ms DESC
LIMIT 5

/* Assume that Slow Queries are : '10000000-0000-0000-0000-000000000001' and '10000000-0000-0000-0000-000000000002' */

/* Query all nodes part of the super_cool_replicated_cluster cluster and identify all the
sub-queries associated with a certain slow query */
SELECT
    type,
    event_time,
    query_duration_ms,
    query,
    read_rows,
    length(columns) as num_columns,
    length(partitions) as num_partitions,
    length(tables) as num_tables,
    peak_threads_usage,
    tables
FROM clusterAllReplicas(super_cool_replicated_cluster, system.query_log)
WHERE initial_query_id='10000000-0000-0000-0000-000000000001'
ORDER BY query_duration_ms DESC

/* Get the trace information for the specific query from all nodes in the  super_cool_replicated_cluster */
SELECT *
FROM clusterAllReplicas(super_cool_replicated_cluster, system.trace_log)
WHERE query_id='10000000-0000-0000-0000-000000000001'
