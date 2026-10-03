-- NDB tables through the chart's MySQL Server: rows live in the data nodes (node group 0,
-- 2 replicas), not in the mysqld pod.
DROP DATABASE IF EXISTS demo;
CREATE DATABASE demo;
USE demo;
CREATE TABLE accounts (
  id      INT         NOT NULL PRIMARY KEY,
  owner   VARCHAR(32) NOT NULL,
  balance INT         NOT NULL
) ENGINE=NDB;
INSERT INTO accounts
WITH RECURSIVE n (id) AS (SELECT 1 UNION ALL SELECT id + 1 FROM n WHERE id < 32)
SELECT id, CONCAT('user', LPAD(id, 2, '0')), 100 FROM n;

-- data nodes and their node group; the chart's third slot (node 3) is inactive
SELECT node_id, status FROM ndbinfo.nodes;
SELECT node_id, group_id AS node_group FROM ndbinfo.membership;

-- every fragment is held by both data nodes
SELECT node_id, fragment_num, fixed_elem_count AS row_count
FROM ndbinfo.memory_per_fragment WHERE fq_name = 'demo/def/accounts'
ORDER BY fragment_num, node_id;

-- a transfer in one transaction
START TRANSACTION;
UPDATE accounts SET balance = balance - 30 WHERE id = 1;
UPDATE accounts SET balance = balance + 30 WHERE id = 2;
COMMIT;
SELECT id, owner, balance FROM accounts WHERE id <= 2;
SELECT SUM(balance) AS total FROM accounts;
