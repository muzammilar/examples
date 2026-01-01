-- Create a table with
CREATE TABLE users (
    creation_time DateTime64,
    id UUID,
    name String,
  -- INDEX idx_id(id) TYPE minmax GRANULARITY 1,
  )
ENGINE=ReplacingMergeTree()
PARTITION BY toYYYYMM(creation_time)
ORDER BY (id, name)
SETTINGS index_granularity=4;

-- Insert data with a random generator (using schema)
INSERT INTO users
SELECT
    toDateTime64('2022-01-01 00:00:00.000', 3) +  toIntervalDay(4*days),
    id,
    name
FROM generateRandom(
    'days UInt8, id UUID, name String',
    1234 -- random seed for reproducible results
) limit 5000000;

-- Insert deterministic data
INSERT INTO users VALUES ('2030-01-01 00:00:00.000', 'ffffffff-ffff-ffff-ffff-fffffffffffd', 'john doe');

-- Insert deterministic data
INSERT INTO users VALUES ('2029-01-01 00:00:00.000', '00000000-0000-0000-0000-000000000002', 'john doe');

-- Query the data for deterministic data and see how indexes behave
EXPLAIN indexes=1 SELECT * FROM users where id='00000000-0000-0000-0000-000000000002';

-- Create the index
ALTER TABLE users ADD INDEX idx_id(id) TYPE minmax GRANULARITY 1;

-- Materialize the index for deterministic data
ALTER TABLE users MATERIALIZE INDEX idx_id;

-- Check if the index is materialized
SELECT parts_to_do, * FROM system.mutations where table='users' and is_done=0;

-- Check if the index is materialized
SELECT * FROM system.data_skipping_indices WHERE table= 'users';

-- Check if the index is materialized
SHOW CREATE TABLE users;

-- Check if the index is materialized
SELECT * FROM system.data_skipping_indices where table='users';


;;
