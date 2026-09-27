-- YCQL keyspaces take their replication from the universe (RF=3)
CREATE KEYSPACE IF NOT EXISTS demo;
CREATE TABLE IF NOT EXISTS demo.failover (phase text PRIMARY KEY, at timestamp);
INSERT INTO demo.failover (phase, at) VALUES ('before', currenttimestamp());
