-- Through HAProxy :5001, i.e. on one of the replicas (round-robin).
\timing on
SELECT inet_server_addr() AS replica_ip, pg_is_in_recovery() AS in_recovery,
       pg_last_wal_replay_lsn() AS replayed_lsn, now() - pg_last_xact_replay_timestamp() AS behind;

-- the hypertable, its columnstore chunks and the continuous aggregate are all here
SELECT count(*) AS rows FROM conditions;
SELECT sensor_id, round(avg(avg_temp)::numeric, 3) AS avg_temp, sum(n) AS readings
FROM conditions_hourly WHERE sensor_id <= 3 GROUP BY sensor_id ORDER BY sensor_id;
EXPLAIN (COSTS OFF)
SELECT sensor_id, max(temperature) FROM conditions
WHERE time < now() - interval '5 days' AND time > now() - interval '6 days' GROUP BY sensor_id;

-- a replica is read-only; background jobs (policies, cagg refreshes) only run on the primary
\set ON_ERROR_STOP 0
INSERT INTO conditions VALUES (now(), 1, 0);
\set ON_ERROR_STOP 1
SELECT count(*) AS jobs_defined FROM timescaledb_information.jobs;
