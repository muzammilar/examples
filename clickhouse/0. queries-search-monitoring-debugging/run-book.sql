

/* The following queries are taken from https://www.altinity.com/blog/2020/5/12/sql-for-clickhouse-dba */
/* Please read the original blog post before using */

/* Get currently running queries on the server, not all connections to the server.*/
SHOW PROCESSLIST; 

/* Get currently running queries on the server with user information, not all connections to the server.*/
SELECT query_id, user, address, elapsed, query   FROM system.processes ORDER BY query_id;

/* Get the number of total connections */
SELECT * FROM system.metrics WHERE metric LIKE '%Connection';

/* Kill a specifc query */
KILL QUERY WHERE query_id='query_id';

/* Check the asynchronous batch Update/Delete jobs aka mutations */
SELECT * FROM system.mutations; 

/* Check the asynchronous batch Update/Delete jobs aka mutations - Only length and not actual parts*/
SELECT
    database,
    table,
    mutation_id,
    command,
    length(parts_to_do_names),
    latest_failed_part,
    latest_fail_time,
    latest_fail_reason
FROM system.mutations;


/* Kill the batch Update/Delete jobs aka mutations. Please avoid if you can, since these are background processes */
KILL MUTATION mutation_id = 'trx_id';

/*Get Disk usage by partitions*/
SELECT database, table, partition, name part_name, active, bytes_on_disk 
  FROM system.parts ORDER BY database, table, partition, name;

/*With multi-disk configurations, it is also useful to look at used space by a particular disk or volume*/
SELECT * FROM system.disks;

/* Force a table to Merge (good for compression). I would not recommend it as it takes a long time */
OPTIMIZE TABLE table [PARTITION partition] [FINAL];

/* Get longer running queries - Query information is stored in the metrics_log table */
SELECT toStartOfMinute(event_time) AS time, 
       sum(ProfileEvent_UserTimeMicroseconds) AS user_time_microseconds, 
       bar(user_time, 0, 60000000, 80) AS bar 
  FROM system.metric_log 
 WHERE event_date = today() 
 GROUP BY time ORDER BY time;


/* Check if all replicas are okay - Query taken from  https://clickhouse.tech/docs/en/operations/system_tables/#system_tables-replicas */
SELECT
    database,
    table,
    is_leader,
    is_readonly,
    is_session_expired,
    future_parts,
    parts_to_check,
    columns_version,
    queue_size,
    inserts_in_queue,
    merges_in_queue,
    log_max_index,
    log_pointer,
    total_replicas,
    active_replicas
FROM system.replicas
WHERE
       is_readonly
    OR is_session_expired
    OR future_parts > 20
    OR parts_to_check > 10
    OR queue_size > 20
    OR inserts_in_queue > 10
    OR log_max_index - log_pointer > 10
    OR total_replicas < 2
    OR active_replicas < total_replicas;
