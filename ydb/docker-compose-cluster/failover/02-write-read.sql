-- $phase is passed by `make failover` (-p '$phase="..."')
DECLARE $phase AS Utf8;
UPSERT INTO failover (phase, at) VALUES ($phase, CurrentUtcTimestamp());
SELECT * FROM failover ORDER BY at;
