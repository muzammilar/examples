-- Kafka Producer Setup
-- Architecture: Refreshable MV -> Kafka Engine (Direct)
-- This approach sends data directly to Kafka from the refreshable materialized view
-- All producer tables are created in the 'production' database

-- Step 1: Create a Kafka engine table that acts as a producer
-- This table sends data to the 'user_analytics' Kafka topic
CREATE TABLE IF NOT EXISTS production.kafka_user_analytics_producer
(
    -- _key is a special column that can be used to define the partition
    -- _key String,
    -- Timestamp when the analytics record was generated
    generated_at DateTime,
    -- User email from the dictionary
    email String,
    -- Email domain extracted from the email address
    email_domain String,
    -- User's full name
    full_name String,
    -- Days since user was created
    days_since_created Int32,
    -- Event timestamp when the analytics event occurred
    event_timestamp DateTime,
    -- Random event count (0-50) representing activity level
    event_count UInt64
)
-- Use kafka_producer_config named collection (configured in clickhouse/config/named_collections.xml)
-- Named collection includes: broker list, consumer group, topic, and format
ENGINE = Kafka(kafka_producer_config);

-- Step 2: Create a refreshable materialized view that generates analytics and sends directly to Kafka
-- This view queries the users_dict dictionary and writes directly to the Kafka producer
-- REFRESH: Automatically refreshes every 60 seconds to generate and publish new analytics to Kafka
CREATE MATERIALIZED VIEW IF NOT EXISTS production.user_analytics_generator
REFRESH EVERY 60 SECOND
TO production.kafka_user_analytics_producer
AS
SELECT
    now() AS generated_at,
    email,
    -- Extract domain from email address (everything after @)
    splitByChar('@', email)[2] AS email_domain,
    full_name,
    -- Calculate days since user was created
    dateDiff('day', created_at, now()) AS days_since_created,
    -- Generate random timestamp between now and 10 days ago
    -- Subtract random number of seconds (0 to 10 days worth of seconds)
    now() - toIntervalSecond(rand() % (10 * 24 * 3600)) AS event_timestamp,
    -- Generate random event count between 0 and 50 as UInt64 using :: syntax
    (rand() % 51)::UInt64 AS event_count
FROM dictionary('users_dict')
-- Only include users with valid email addresses
WHERE email != '';
