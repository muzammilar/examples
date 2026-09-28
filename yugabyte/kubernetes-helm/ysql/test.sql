-- Hash sharding: rows are spread over tablets by yb_hash_code(id) (0..65535).
DROP TABLE IF EXISTS accounts, events;
CREATE TABLE accounts (id int, owner text, balance int, PRIMARY KEY (id HASH))
  SPLIT INTO 3 TABLETS;
-- Range sharding: rows stay ordered by ts; tablets are pre-split at fixed keys.
CREATE TABLE events (ts timestamptz, kind text, PRIMARY KEY (ts ASC))
  SPLIT AT VALUES (('2026-01-01'), ('2026-07-01'));
CREATE INDEX accounts_owner_idx ON accounts (owner);

INSERT INTO accounts VALUES (1, 'alice', 100), (2, 'bob', 100), (3, 'carol', 100);
INSERT INTO events VALUES ('2025-12-31', 'a'), ('2026-03-01', 'b'), ('2026-09-01', 'c');

-- Distributed ACID transaction across tablets
BEGIN;
UPDATE accounts SET balance = balance - 30 WHERE id = 1;
UPDATE accounts SET balance = balance + 30 WHERE id = 2;
COMMIT;

SELECT id, yb_hash_code(id) AS hash, owner, balance FROM accounts ORDER BY id;
-- Tablets: hash ranges (accounts) or key ranges (events), leader and replicas
SELECT relname, start_hash_code AS hash_from, end_hash_code AS hash_to,
       start_range, end_range, regexp_replace(leader, '[.:].*', '') AS leader,
       (SELECT array_agg(regexp_replace(r, '[.:].*', '') ORDER BY r) FROM unnest(replicas) r) AS replicas
  FROM yb_tablet_metadata WHERE relname IN ('accounts', 'events')
  ORDER BY relname, start_hash_code, start_range NULLS FIRST;

-- Index lookup; DIST shows the storage-layer (DocDB) read requests
EXPLAIN (ANALYZE, DIST, COSTS OFF, TIMING OFF, SUMMARY OFF)
  SELECT * FROM accounts WHERE owner = 'bob';

SELECT regexp_replace(host, '\..*', '') AS host, cloud, region, zone FROM yb_servers() ORDER BY 1;
