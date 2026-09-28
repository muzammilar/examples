-- TTL, part 1 (`make test` waits 6 s, then runs read.cql). Row-level TTL is not allowed on
-- tables with a secondary index, so this table has none.
CREATE KEYSPACE IF NOT EXISTS demo;
USE demo;
DROP TABLE IF EXISTS sessions;

-- the table default: a row expires 5 s after it was written, unless the write says otherwise
CREATE TABLE sessions (id text PRIMARY KEY, username text, secret text) WITH default_time_to_live = 5;
INSERT INTO sessions (id, username, secret) VALUES ('s1', 'alice', 'a1');
INSERT INTO sessions (id, username, secret) VALUES ('s2', 'bob', 'b1') USING TTL 3600;
-- a column TTL: bob's secret expires in 2 s, his row stays
UPDATE sessions USING TTL 2 SET secret = 'b2' WHERE id = 's2';

SELECT id, username, secret, TTL(username), TTL(secret) FROM sessions;
