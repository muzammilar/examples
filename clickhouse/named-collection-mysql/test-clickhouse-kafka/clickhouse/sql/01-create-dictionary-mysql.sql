-- Create a dictionary in ClickHouse that sources data from the MySQL users table
-- This dictionary uses the mysql_config named collection for connection details
-- Dictionaries in ClickHouse provide fast key-value lookups with automatic reloading

CREATE DICTIONARY IF NOT EXISTS users_dict
(
    -- Primary key field used for lookups (using email for hash-based lookup)
    email String,
    -- User fields that will be stored in the dictionary
    id UInt32,
    username String,
    full_name String,
    created_at DateTime,
    updated_at DateTime
)
PRIMARY KEY email
-- Use MySQL named collection to connect to the source database
-- The named collection is configured in clickhouse/config/named_collections.xml
-- It contains connection details: host, port, user, password, and database (testdb)
SOURCE(MYSQL(
    NAME 'mysql_config'
    TABLE 'users'
    -- Track updates using the updated_at column to detect changed rows
    UPDATE_FIELD 'updated_at'
))
-- Dictionary layout determines how data is stored in memory
-- HASHED layout is efficient for string-based keys and provides O(1) lookup time
LAYOUT(HASHED())
-- Dynamic lifetime with random interval between MIN and MAX to distribute reload load
-- MIN: minimum reload interval (60 seconds)
-- MAX: maximum reload interval (300 seconds / 5 minutes)
-- Random intervals prevent all dictionaries from reloading simultaneously
LIFETIME(MIN 60 MAX 300);
