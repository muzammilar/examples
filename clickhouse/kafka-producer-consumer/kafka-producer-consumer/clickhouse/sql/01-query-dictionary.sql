-- Query to read all data from the users_dict dictionary
-- This demonstrates how to access dictionary data in ClickHouse

-- View dictionary metadata and status
SELECT
    name,
    status,
    origin,
    type,
    key.names AS key_columns,
    attribute.names AS attribute_columns,
    element_count,
    load_factor,
    bytes_allocated,
    last_successful_update_time
FROM system.dictionaries
WHERE name = 'users_dict';

-- Retrieve all data from the dictionary using the dictionary() table function
-- This is the most straightforward way to get all dictionary data
SELECT *
FROM dictionary('users_dict');

-- Retrieve specific values from the dictionary using dictGet functions with a WITH clause
-- Example: Get user details by email lookup using a cleaner approach
WITH 'john@example.com' AS lookup_email
SELECT
    lookup_email AS email,
    dictGet('users_dict', 'id', lookup_email) AS id,
    dictGet('users_dict', 'username', lookup_email) AS username,
    dictGet('users_dict', 'full_name', lookup_email) AS full_name,
    dictGet('users_dict', 'created_at', lookup_email) AS created_at,
    dictGet('users_dict', 'updated_at', lookup_email) AS updated_at;
