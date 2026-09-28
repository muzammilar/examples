-- NDB tables live in the data nodes, not in this MySQL Server: any MySQL Server
-- (or NDB API / REST client) connected to the cluster sees the same rows.
DROP DATABASE IF EXISTS demo;
CREATE DATABASE demo;
USE demo;

CREATE TABLE accounts (
  id      INT         NOT NULL PRIMARY KEY,
  owner   VARCHAR(32) NOT NULL,
  balance INT         NOT NULL,
  KEY (owner)
) ENGINE=NDB;

INSERT INTO accounts VALUES
  (1, 'alice', 100), (2, 'bob', 100), (3, 'carol', 100), (4, 'dave', 100),
  (5, 'erin', 100), (6, 'frank', 100), (7, 'grace', 100), (8, 'heidi', 100);

SELECT table_name, engine, table_rows
FROM information_schema.tables WHERE table_schema = 'demo';
