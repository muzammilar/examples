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

-- 32 accounts with balance 100: enough rows that every fragment gets some (sql/02)
INSERT INTO accounts (id, owner, balance)
WITH RECURSIVE n (id) AS (SELECT 1 UNION ALL SELECT id + 1 FROM n WHERE id < 32)
SELECT id, COALESCE(ELT(id, 'alice', 'bob', 'carol', 'dave', 'erin', 'frank', 'grace', 'heidi'),
                    CONCAT('user', LPAD(id, 2, '0'))), 100
FROM n;

SELECT table_name, engine, table_rows
FROM information_schema.tables WHERE table_schema = 'demo';
