-- Self JOINs in Incremental MV - How to not use clickhouse
-- Since all tables here are MergeTrees, we see duplicates in the results. Generally, self join is performed for aggregating merge trees.
-- Why duplicates? In a batch insert, each row sees the others via the view. Row 2 joins to row 1 (first UNION branch), and row 1 joins to row 2 (second UNION branch) - both fire in the same batch.
-- The UNION ALL primarily allows for a self join. This is very simliar to performing recursion in clickhouse and flattening a graph/converting (all parents) into array.
-- This is bad for performance, and scale. You should probably just use a refreshable materialized view and perform a union on two different tables.

-- Source table
CREATE TABLE events (
  event_id UInt64,
  user_id UInt64,
  event_type String,
  timestamp DateTime
) ENGINE = MergeTree()
ORDER BY (user_id, timestamp);

-- Regular view on the source table (required for self-join in MV)
CREATE VIEW events_view AS
SELECT *
FROM events;

-- Target table
CREATE TABLE user_event_pairs (
  user_id UInt64,
  first_event_type String,
  first_timestamp DateTime,
  second_event_type String,
  second_timestamp DateTime
) ENGINE = MergeTree()
ORDER BY (user_id, first_timestamp);

-- Materialized view: joins incoming rows against the view
CREATE MATERIALIZED VIEW user_event_pairs_mv TO user_event_pairs AS -- New row is the "second" event, find earlier events via view
SELECT new.user_id,
  v.event_type AS first_event_type,
  v.timestamp AS first_timestamp,
  new.event_type AS second_event_type,
  new.timestamp AS second_timestamp
FROM events AS new  -- freshly inserted data as part of MV
  INNER JOIN events_view AS v ON new.user_id = v.user_id
  AND v.timestamp < new.timestamp
  AND v.timestamp >= new.timestamp - INTERVAL 1 HOUR
UNION ALL
-- New row is the "first" event, find later events via view
SELECT new.user_id,
  new.event_type AS first_event_type,
  new.timestamp AS first_timestamp,
  v.event_type AS second_event_type,
  v.timestamp AS second_timestamp
FROM events AS new -- freshly inserted data as part of MV
  INNER JOIN events_view AS v ON new.user_id = v.user_id -- note: this inner join is even more expensive, maybe use a WHERE
  AND v.timestamp > new.timestamp
  AND v.timestamp <= new.timestamp + INTERVAL 1 HOUR;
-- Test inserts
INSERT INTO events
VALUES (1, 100, 'page_view', '2024-01-15 10:00:00');
INSERT INTO events
VALUES (2, 100, 'click', '2024-01-15 10:30:00');
INSERT INTO events
VALUES (3, 100, 'purchase', '2024-01-15 10:50:00');
-- Check results
SELECT *
FROM user_event_pairs
ORDER BY first_timestamp,
  second_timestamp;


-- Muzammil's solution
INSERT INTO events
VALUES (1, 100, 'page_view', '2024-01-15 10:00:00'),
  (2, 100, 'click', '2024-01-15 10:15:00'),
  (3, 100, 'add_to_cart', '2024-01-15 10:30:00'),
  (4, 100, 'purchase', '2024-01-15 10:45:00');

INSERT INTO events
VALUES (1, 100, 'page_view', '2024-01-15 10:00:00'),
  (2, 100, 'click', '2024-01-15 10:15:00'),
  (3, 100, 'add_to_cart', '2024-01-15 10:30:00'),
  (4, 100, 'purchase', '2024-01-15 10:45:00');


INSERT INTO events
VALUES (1, 100, 'page_view', '2024-01-15 10:00:00'),
  (2, 100, 'click', '2024-01-15 10:15:00'),
  (3, 100, 'add_to_cart', '2024-01-15 10:30:00'),
  (4, 100, 'purchase', '2024-01-15 10:45:00');

-- Check pairs created
SELECT user_id,
  first_event_type,
  first_timestamp,
  second_event_type,
  second_timestamp
FROM user_event_pairs
ORDER BY first_timestamp,
  second_timestamp;

-- Count pairs per event combination to see duplicates
SELECT first_event_type,
  second_event_type,
  count() AS cnt
FROM user_event_pairs
GROUP BY first_event_type,
  second_event_type
ORDER BY first_event_type,
  second_event_type;
