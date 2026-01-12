-- Query to view Kafka analytics data
-- This file demonstrates how to query the consumed analytics data
-- All queries are performed on tables in the 'consumption' database

-- View summary statistics about consumed analytics
SELECT
    'Total Analytics Records' AS metric,
    count() AS value
FROM consumption.user_analytics
UNION ALL
SELECT
    'Unique Emails Analyzed' AS metric,
    count(DISTINCT email) AS value
FROM consumption.user_analytics
UNION ALL
SELECT
    'Unique Email Domains' AS metric,
    count(DISTINCT email_domain) AS value
FROM consumption.user_analytics
ORDER BY metric;

-- View the most recent analytics records
SELECT
    generated_at,
    email,
    email_domain,
    full_name,
    days_since_created,
    event_timestamp,
    event_count,
    consumed_at
FROM consumption.user_analytics
ORDER BY consumed_at DESC
LIMIT 10;

-- Aggregate analytics by email domain
SELECT
    email_domain,
    count() AS user_count,
    avg(days_since_created) AS avg_days_since_created,
    sum(event_count) AS total_events,
    avg(event_count) AS avg_event_count,
    max(event_timestamp) AS last_event_timestamp
FROM consumption.user_analytics
GROUP BY email_domain
ORDER BY user_count DESC;
