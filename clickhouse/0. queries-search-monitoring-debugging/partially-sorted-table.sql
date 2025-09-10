/*
You can create tables that have a partial sorting by using some aggregate functions.
For example, you can sort the data on a timestamp field, by sorting only by the hour or by the day
instead of the exact second.
The biggest advantage of this approach is that this allows your subsequent fields
in ORDER BY to co-locate their data.
*/

/* Create a table - We sort on the event time twice,
the first sort is to sort by day, and second sort is within the day for a given user_id */
CREATE TABLE my_events
(
    `system_id` UInt64,
    `event_time` DateTime,
    `user_id` UInt64,
    `event_type` String,
    `value` UInt64,
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_time)
ORDER BY (system_id, toStartOfDay(event_time), user_id, event_time);
-- ORDER BY (system_id, toStartOfDay(event_time), user_id, toStartOfHour(event_time)); -- This will also work


/* Add data */
INSERT INTO my_events (system_id, event_time, user_id, value) WITH
    365 * 1 AS start_intvl,
    365 * 2 AS num_days,
    ((60 * 60) * 24) AS one_day,
    1000000 AS num_data_points
SELECT
    randUniform(1,10) AS system_id,
    toDateTime(now() - toIntervalDay(start_intvl)) + toInt64(randUniform(0, one_day * num_days)) AS event_time,
    randUniform(5, 20) AS user_id,
    rand() AS value
FROM system.numbers
LIMIT num_data_points;


/* See Partially sorted data */
SELECT *
FROM my_events;

/* Check your search (that explain command shows the indices) */
EXPLAIN indexes = 1
SELECT count()
FROM my_events
WHERE (event_time > (now() - toIntervalMinute(5))) AND (event_time < now() + toIntervalDay(5));
