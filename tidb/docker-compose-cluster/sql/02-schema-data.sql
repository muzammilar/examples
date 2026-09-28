-- Schema with AUTO_RANDOM keys and generated data (no external files).
DROP DATABASE IF EXISTS demo;
CREATE DATABASE demo;
USE demo;

-- AUTO_RANDOM puts random shard bits in the top of the key, so inserts are spread
-- over many regions instead of all hitting the last one (no write hotspot).
CREATE TABLE customers (
  id      BIGINT PRIMARY KEY AUTO_RANDOM,
  name    VARCHAR(64) NOT NULL,
  country CHAR(2)     NOT NULL
);
CREATE TABLE orders (
  id          BIGINT PRIMARY KEY AUTO_RANDOM,
  customer_id BIGINT        NOT NULL,
  amount      DECIMAL(10,2) NOT NULL,
  created_at  DATETIME      NOT NULL,
  KEY idx_customer (customer_id)
);
CREATE TABLE accounts (
  id      INT PRIMARY KEY,
  owner   VARCHAR(32) NOT NULL,
  balance DECIMAL(10,2) NOT NULL
);

-- numbers 1..1000 from a recursive CTE
CREATE TABLE seq (n INT PRIMARY KEY);
INSERT INTO seq WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 1000) SELECT n FROM r;

-- The shard bits are derived from the transaction's start timestamp, so every row
-- of one statement gets the same shard: load in 8 statements, like real traffic.
INSERT INTO customers (name, country) SELECT CONCAT('customer-', n), ELT(1 + n % 5, 'DE', 'US', 'JP', 'BR', 'IN') FROM seq WHERE n <= 500 AND n % 8 = 0;
INSERT INTO customers (name, country) SELECT CONCAT('customer-', n), ELT(1 + n % 5, 'DE', 'US', 'JP', 'BR', 'IN') FROM seq WHERE n <= 500 AND n % 8 = 1;
INSERT INTO customers (name, country) SELECT CONCAT('customer-', n), ELT(1 + n % 5, 'DE', 'US', 'JP', 'BR', 'IN') FROM seq WHERE n <= 500 AND n % 8 = 2;
INSERT INTO customers (name, country) SELECT CONCAT('customer-', n), ELT(1 + n % 5, 'DE', 'US', 'JP', 'BR', 'IN') FROM seq WHERE n <= 500 AND n % 8 = 3;
INSERT INTO customers (name, country) SELECT CONCAT('customer-', n), ELT(1 + n % 5, 'DE', 'US', 'JP', 'BR', 'IN') FROM seq WHERE n <= 500 AND n % 8 = 4;
INSERT INTO customers (name, country) SELECT CONCAT('customer-', n), ELT(1 + n % 5, 'DE', 'US', 'JP', 'BR', 'IN') FROM seq WHERE n <= 500 AND n % 8 = 5;
INSERT INTO customers (name, country) SELECT CONCAT('customer-', n), ELT(1 + n % 5, 'DE', 'US', 'JP', 'BR', 'IN') FROM seq WHERE n <= 500 AND n % 8 = 6;
INSERT INTO customers (name, country) SELECT CONCAT('customer-', n), ELT(1 + n % 5, 'DE', 'US', 'JP', 'BR', 'IN') FROM seq WHERE n <= 500 AND n % 8 = 7;

-- 500 customers x 40 = 20000 orders, again in 8 statements
INSERT INTO orders (customer_id, amount, created_at) SELECT c.id, ROUND(1 + RAND() * 499, 2), NOW() - INTERVAL FLOOR(RAND() * 365) DAY FROM customers c JOIN seq ON seq.n <= 40 WHERE c.id % 8 = 0;
INSERT INTO orders (customer_id, amount, created_at) SELECT c.id, ROUND(1 + RAND() * 499, 2), NOW() - INTERVAL FLOOR(RAND() * 365) DAY FROM customers c JOIN seq ON seq.n <= 40 WHERE c.id % 8 = 1;
INSERT INTO orders (customer_id, amount, created_at) SELECT c.id, ROUND(1 + RAND() * 499, 2), NOW() - INTERVAL FLOOR(RAND() * 365) DAY FROM customers c JOIN seq ON seq.n <= 40 WHERE c.id % 8 = 2;
INSERT INTO orders (customer_id, amount, created_at) SELECT c.id, ROUND(1 + RAND() * 499, 2), NOW() - INTERVAL FLOOR(RAND() * 365) DAY FROM customers c JOIN seq ON seq.n <= 40 WHERE c.id % 8 = 3;
INSERT INTO orders (customer_id, amount, created_at) SELECT c.id, ROUND(1 + RAND() * 499, 2), NOW() - INTERVAL FLOOR(RAND() * 365) DAY FROM customers c JOIN seq ON seq.n <= 40 WHERE c.id % 8 = 4;
INSERT INTO orders (customer_id, amount, created_at) SELECT c.id, ROUND(1 + RAND() * 499, 2), NOW() - INTERVAL FLOOR(RAND() * 365) DAY FROM customers c JOIN seq ON seq.n <= 40 WHERE c.id % 8 = 5;
INSERT INTO orders (customer_id, amount, created_at) SELECT c.id, ROUND(1 + RAND() * 499, 2), NOW() - INTERVAL FLOOR(RAND() * 365) DAY FROM customers c JOIN seq ON seq.n <= 40 WHERE c.id % 8 = 6;
INSERT INTO orders (customer_id, amount, created_at) SELECT c.id, ROUND(1 + RAND() * 499, 2), NOW() - INTERVAL FLOOR(RAND() * 365) DAY FROM customers c JOIN seq ON seq.n <= 40 WHERE c.id % 8 = 7;

INSERT INTO accounts VALUES (1, 'alice', 100.00), (2, 'bob', 100.00), (3, 'carol', 100.00);

ANALYZE TABLE customers, orders;
SELECT (SELECT COUNT(*) FROM customers) AS customers, (SELECT COUNT(*) FROM orders) AS orders, (SELECT COUNT(*) FROM accounts) AS accounts;
