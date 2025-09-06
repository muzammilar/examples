/* Get Partitions by each table - Recommended total number of partitions for a table is under 1000..10000*/
SELECT
    database,
    table,
    count() AS num_parts,
    formatReadableSize(sum(bytes)) AS table_size,
    formatReadableSize(sum(data_compressed_bytes))  AS table_size_compressed,
    formatReadableSize(sum(data_uncompressed_bytes))  AS table_size_uncompressed,
    formatReadableSize(sum(primary_key_bytes_in_memory))  AS table_primary_key_size_in_memory,
    sum(marks) AS table_marks,
    sum(rows) AS table_num_rows,
    formatReadableSize(sum(marks_size)) AS table_marks_size
FROM system.parts
WHERE (database != 'system') AND active
GROUP BY (database, table)
ORDER BY sum(data_uncompressed_bytes) DESC;


/* Get Partition deletion information and size for single table. Useful for deleting old partitions and checking if TTL change happened correctly */
WITH
    'my_database_v1' AS database_name,
    'my_table_local' AS table_name
SELECT
    name,
    partition_id,
    active,
    database,
    table,
    marks,
    rows,
    remove_time,
    min_date,
    max_date,
    delete_ttl_info_min,
    delete_ttl_info_max,
    formatReadableSize(bytes) AS part_size,
    formatReadableSize(data_compressed_bytes) AS part_size_compressed,
    formatReadableSize(data_uncompressed_bytes) AS part_size_uncompressed,
    formatReadableSize(primary_key_bytes_in_memory) AS part_primary_key_size_in_memory,
    formatReadableSize(marks_size) AS part_marks_size
FROM system.parts
WHERE (database = database_name) AND (table = table_name)
ORDER BY remove_time DESC;


/* Get Compression Information By Database */
SELECT
    database,
    count() AS num_parts,
    formatReadableSize(sum(bytes)) AS table_size,
    formatReadableSize(sum(data_compressed_bytes))  AS table_size_compressed,
    formatReadableSize(sum(data_uncompressed_bytes))  AS table_size_uncompressed,
    formatReadableSize(sum(primary_key_bytes_in_memory))  AS table_primary_key_size_in_memory,
    sum(marks) AS table_marks,
    sum(rows) AS table_num_rows,
    formatReadableSize(sum(marks_size)) AS table_marks_size
FROM system.parts
GROUP BY database
ORDER BY sum(data_uncompressed_bytes) DESC;

/* Get Total Compression Information */
SELECT
    count() AS num_parts,
    formatReadableSize(sum(bytes)) AS table_size,
    formatReadableSize(sum(data_compressed_bytes))  AS table_size_compressed,
    formatReadableSize(sum(data_uncompressed_bytes))  AS table_size_uncompressed,
    formatReadableSize(sum(primary_key_bytes_in_memory))  AS table_primary_key_size_in_memory,
    sum(marks) AS table_marks,
    sum(rows) AS table_num_rows,
    formatReadableSize(sum(marks_size)) AS table_marks_size
FROM system.parts
ORDER BY sum(data_uncompressed_bytes) DESC;


/* Get Compression information for all tables and the fields */
SELECT
    database,
    table,
    name,
    formatReadableSize(sum(data_compressed_bytes)) AS compressed,
    formatReadableSize(sum(data_uncompressed_bytes)) AS uncompressed,
    (sum(data_compressed_bytes) * 100) / sum(data_uncompressed_bytes) AS compress_ratio
FROM system.columns
GROUP BY database, table, name
ORDER BY compress_ratio DESC;

/* Get Total disk usage */
SELECT
    name,
    path,
    formatReadableSize(free_space) AS free,
    formatReadableSize(total_space) AS total,
    type
FROM system.disks;
