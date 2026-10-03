-- Retention: drop whole chunks older than 14 days (a DROP TABLE per chunk, no DELETE + VACUUM).
-- The continuous aggregates keep their history: they only change on an explicit refresh of that range.
\timing on

SELECT count(*) AS chunks_before, (SELECT min(time)::date FROM conditions) AS oldest_row
FROM show_chunks('conditions');

SELECT count(*) AS dropped FROM drop_chunks('conditions', older_than => now() - interval '14 days');

SELECT count(*) AS chunks_after, (SELECT min(time)::date FROM conditions) AS oldest_row,
       pg_size_pretty(hypertable_size('conditions')) AS hypertable_size
FROM show_chunks('conditions');
SELECT min(day)::date AS daily_cagg_oldest_day, count(*) AS daily_rows FROM conditions_daily;

-- the policy that does this every day
SELECT add_retention_policy('conditions', drop_after => interval '14 days', if_not_exists => true) > 0 AS policy_added;

-- all background jobs: columnstore, cagg refresh and retention policies
SELECT job_id, proc_name, schedule_interval, hypertable_name, config::text
FROM timescaledb_information.jobs WHERE job_id >= 1000 ORDER BY job_id;
SELECT job_id, total_runs, total_failures, last_run_status
FROM timescaledb_information.job_stats ORDER BY job_id;
