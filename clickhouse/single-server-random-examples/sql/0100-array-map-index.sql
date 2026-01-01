-- Basis example to use array map and indexes
-- Clickhouse indexes are very preices
-- Note: Bloom filter indexes are based on the query. They're also slower than the other indexes
-- Create the table with the bloom filter index
CREATE TABLE your_table
(
    id UInt64,
    datas Array(Tuple(
        uuid UUID,
        deleted UInt8
    )) DEFAULT [],
    -- other columns...

    -- Bloom filter index on the uuid field within the array
    -- this index will be used for the arrayExists query
    INDEX datas_uuid_bloom arrayMap(x -> x.1, datas) TYPE bloom_filter(0.01) GRANULARITY 1,

    -- Another way to create the index
    -- this index will be used for the has query (but not for arrayExists)
    INDEX datas_uuid_bloom_second datas.uuid TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = MergeTree()
ORDER BY id;

-- Add bloom filter index to existing table
ALTER TABLE your_table
ADD INDEX datas_uuid_bloom_third arrayMap(x -> x.1, datas) TYPE bloom_filter(0.01) GRANULARITY 1;

-- Materialize the index for existing data
-- If an index is created after data is inserted, it needs to be materialized
ALTER TABLE your_table MATERIALIZE INDEX datas_uuid_bloom_third;

-- Insert data
INSERT INTO your_table
  SELECT
      number as id,
      [
          (generateUUIDv4(), rand() % 2),
          (generateUUIDv4(), rand() % 2),
          (generateUUIDv4(), rand() % 2),
          (generateUUIDv4(), rand() % 2),
          (generateUUIDv4(), rand() % 2),
          (generateUUIDv4(), rand() % 2),
          (generateUUIDv4(), rand() % 2),
          (generateUUIDv4(), rand() % 2),
          (generateUUIDv4(), rand() % 2),
          (generateUUIDv4(), rand() % 2),
          (generateUUIDv4(), rand() % 2),
          (generateUUIDv4(), rand() % 2),
          (generateUUIDv4(), rand() % 2)
      ] as datas
  FROM system.numbers
  LIMIT 10000;


-- Query the data
EXPLAIN indexes = 1
SELECT *
FROM your_table
WHERE has(datas.uuid, toUUID('19d741d2-fb86-4630-b80f-5f43cb0ba100'));


;;
