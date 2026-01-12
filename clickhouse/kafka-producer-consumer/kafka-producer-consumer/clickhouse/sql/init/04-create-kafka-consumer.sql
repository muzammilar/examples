-- Kafka Consumer Setup
-- This file creates a Kafka consumer table and a materialized view to persist consumed data
-- All consumer tables are created in the 'consumption' database

-- Create a Kafka engine table that acts as a consumer
-- This table reads data from the 'user_analytics' Kafka topic
CREATE TABLE IF NOT EXISTS consumption.kafka_user_analytics_consumer
(
    -- Timestamp when the analytics record was generated
    generated_at DateTime,
    -- User email from the analytics
    email String,
    -- Email domain extracted from the email address
    email_domain String,
    -- User's full name
    full_name String,
    -- Days since user was created
    days_since_created Int32,
    -- Event timestamp when the analytics event occurred (randomized between now and 10 days ago)
    event_timestamp DateTime,
    -- Random event count (0-50) representing activity level
    event_count UInt64
)
-- Use kafka_consumer_config named collection (configured in clickhouse/config/named_collections.xml)
-- Named collection includes: broker list, consumer group, topic, and format
ENGINE = Kafka(kafka_consumer_config);

-- Create a destination table to store consumed analytics data
-- This table uses MergeTree engine for efficient storage and queries
CREATE TABLE IF NOT EXISTS consumption.user_analytics
(
    -- Timestamp when the analytics record was generated
    generated_at DateTime,
    -- User email from the analytics
    email String,
    -- Email domain extracted from the email address
    email_domain String,
    -- User's full name
    full_name String,
    -- Days since user was created
    days_since_created Int32,
    -- Event timestamp when the analytics event occurred (randomized between now and 10 days ago)
    event_timestamp DateTime,
    -- Random event count (0-50) representing activity level
    event_count UInt64,
    -- Timestamp when the record was consumed and stored
    consumed_at DateTime DEFAULT now()
)
ENGINE = MergeTree()
-- Partition by event date (extracted from event_timestamp) for efficient time-based queries
PARTITION BY toDate(event_timestamp)
-- Order by event timestamp and email for efficient lookups
ORDER BY (event_timestamp, email);

-- Create a materialized view to move data from Kafka consumer to storage table
-- This view automatically processes messages as they arrive from Kafka
CREATE MATERIALIZED VIEW IF NOT EXISTS consumption.user_analytics_consumer_mv
TO consumption.user_analytics
AS
SELECT
    generated_at,
    email,
    email_domain,
    full_name,
    days_since_created,
    event_timestamp,
    event_count,
    now() AS consumed_at
FROM consumption.kafka_user_analytics_consumer;
