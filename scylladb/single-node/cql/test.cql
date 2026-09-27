CREATE KEYSPACE IF NOT EXISTS demo
  WITH replication = {'class': 'NetworkTopologyStrategy', 'replication_factor': 1};
CREATE TABLE IF NOT EXISTS demo.kv (k text PRIMARY KEY, v text);

CONSISTENCY ONE;
INSERT INTO demo.kv (k, v) VALUES ('hello', 'world');
INSERT INTO demo.kv (k, v) VALUES ('foo', 'bar');
SELECT * FROM demo.kv;
DELETE FROM demo.kv WHERE k = 'foo';
SELECT * FROM demo.kv;
DESCRIBE KEYSPACE demo;
