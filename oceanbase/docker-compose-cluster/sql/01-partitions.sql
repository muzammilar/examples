-- Runs in the MySQL-mode user tenant `test` (root@test), not in `sys`.
CREATE DATABASE IF NOT EXISTS demo;
USE demo;
DROP TABLE IF EXISTS orders, accounts;

-- HASH partitions: rows spread over 4 tablets by account id.
CREATE TABLE accounts (
  id      BIGINT PRIMARY KEY,
  owner   VARCHAR(32) NOT NULL,
  balance DECIMAL(12, 2) NOT NULL
) PARTITION BY HASH(id) PARTITIONS 4;

-- RANGE partitions: one tablet per quarter; the partition key must be part of the primary key.
CREATE TABLE orders (
  id         BIGINT NOT NULL,
  account_id BIGINT NOT NULL,
  created    DATE NOT NULL,
  amount     DECIMAL(12, 2) NOT NULL,
  PRIMARY KEY (id, created)
) PARTITION BY RANGE COLUMNS(created) (
  PARTITION p2026q1 VALUES LESS THAN ('2026-04-01'),
  PARTITION p2026q2 VALUES LESS THAN ('2026-07-01'),
  PARTITION p2026q3 VALUES LESS THAN ('2026-10-01'),
  PARTITION p2026q4 VALUES LESS THAN ('2027-01-01')
);

-- Generated data: 1000 accounts, 20000 orders spread over 2026.
SET SESSION cte_max_recursion_depth = 100000;
INSERT INTO accounts
  WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < 1000)
  SELECT n, CONCAT('user', n), 1000 FROM s;
INSERT INTO orders
  WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < 20000)
  SELECT n, 1 + n % 1000, DATE_ADD('2026-01-01', INTERVAL n % 365 DAY), (n % 97) + 0.99 FROM s;

-- Rows per partition (PARTITION (...) reads a single tablet).
SELECT 'accounts' AS tbl, 'p0' AS part, COUNT(*) AS n FROM accounts PARTITION (p0)
UNION ALL SELECT 'accounts', 'p1', COUNT(*) FROM accounts PARTITION (p1)
UNION ALL SELECT 'accounts', 'p2', COUNT(*) FROM accounts PARTITION (p2)
UNION ALL SELECT 'accounts', 'p3', COUNT(*) FROM accounts PARTITION (p3)
UNION ALL SELECT 'orders', 'p2026q1', COUNT(*) FROM orders PARTITION (p2026q1)
UNION ALL SELECT 'orders', 'p2026q2', COUNT(*) FROM orders PARTITION (p2026q2)
UNION ALL SELECT 'orders', 'p2026q3', COUNT(*) FROM orders PARTITION (p2026q3)
UNION ALL SELECT 'orders', 'p2026q4', COUNT(*) FROM orders PARTITION (p2026q4);
