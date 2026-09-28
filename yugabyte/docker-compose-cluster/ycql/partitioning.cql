-- Hash sharding: partition_hash(key) is the row's position (0..65535) in the hash space
-- that the table's tablets split into ranges; token(key) is the Cassandra-style int64 view
-- of the same value. Four tablets here: 0x0000-0x3fff, 0x4000-0x7fff, 0x8000-0xbfff, 0xc000-0xffff.
CREATE KEYSPACE IF NOT EXISTS demo;
USE demo;
DROP TABLE IF EXISTS events;

CREATE TABLE events (id int PRIMARY KEY, kind text) WITH tablets = 4;
INSERT INTO events (id, kind) VALUES (1, 'click');
INSERT INTO events (id, kind) VALUES (2, 'view');
INSERT INTO events (id, kind) VALUES (3, 'click');
INSERT INTO events (id, kind) VALUES (4, 'buy');
INSERT INTO events (id, kind) VALUES (5, 'view');
INSERT INTO events (id, kind) VALUES (6, 'click');
INSERT INTO events (id, kind) VALUES (7, 'view');
INSERT INTO events (id, kind) VALUES (8, 'buy');

SELECT id, kind, partition_hash(id) AS hash, token(id) AS token FROM events;

-- one tablet each: the tablet boundaries (start_key/end_key are the 2-byte hash) and the
-- tservers that hold its three replicas
SELECT start_key, end_key, replica_addresses FROM system.partitions
  WHERE keyspace_name = 'demo' AND table_name = 'events';

-- a parallel full scan issues one query per hash range; each reads a single tablet
SELECT count(*) FROM events WHERE partition_hash(id) >= 0 AND partition_hash(id) < 16384;
SELECT count(*) FROM events WHERE partition_hash(id) >= 16384 AND partition_hash(id) < 32768;
SELECT count(*) FROM events WHERE partition_hash(id) >= 32768 AND partition_hash(id) < 49152;
SELECT count(*) FROM events WHERE partition_hash(id) >= 49152;
-- the same split on token ranges, as Cassandra tools (Spark connector, dsbulk) do it
SELECT count(*) FROM events WHERE token(id) < 0;
SELECT count(*) FROM events WHERE token(id) >= 0;
