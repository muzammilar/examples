-- write the CSV out as Parquet, hive-partitioned by country (parquet/country=XX/*.parquet)
.timer on
COPY events TO 'parquet' (FORMAT parquet, PARTITION_BY (country), OVERWRITE);

SELECT count(*) AS files, list(regexp_extract(file, 'country=(\w+)', 1) ORDER BY file) AS partitions
FROM glob('parquet/*/*.parquet');

-- the same aggregate from CSV and from Parquet
SELECT event_type, count(*) AS n FROM 'events.csv' WHERE country = 'PK' GROUP BY ALL ORDER BY n DESC;
SELECT event_type, count(*) AS n
FROM read_parquet('parquet/*/*.parquet', hive_partitioning = true)
WHERE country = 'PK' GROUP BY ALL ORDER BY n DESC;

-- the country filter is applied to file paths: only country=PK is read
.timer off
EXPLAIN SELECT count(*) FROM read_parquet('parquet/*/*.parquet', hive_partitioning = true)
WHERE country = 'PK';
