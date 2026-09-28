-- QUORUM = 2 of 3 replicas: still reachable with one node down
CREATE KEYSPACE IF NOT EXISTS demo
  WITH replication = {'class': 'NetworkTopologyStrategy', 'replication_factor': 3};
CREATE TABLE IF NOT EXISTS demo.kv (k text PRIMARY KEY, v text);

CONSISTENCY QUORUM;
INSERT INTO demo.kv (k, v) VALUES ('failover', 'written with a node down');
SELECT * FROM demo.kv WHERE k = 'failover';
