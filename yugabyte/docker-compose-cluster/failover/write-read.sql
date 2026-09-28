-- Distributed transaction + read with yb-3 down: its tablet leaders moved to yb-1/yb-2
BEGIN;
INSERT INTO failover (phase) VALUES ('yb-3 down') ON CONFLICT (phase) DO UPDATE SET writes = failover.writes + 1;
UPDATE failover SET writes = writes + 1 WHERE phase = 'before';
COMMIT;
SELECT * FROM failover ORDER BY phase;
SELECT start_hash_code AS hash_from, regexp_replace(leader, '[.:].*', '') AS leader
  FROM yb_tablet_metadata WHERE relname = 'failover' ORDER BY start_hash_code;
