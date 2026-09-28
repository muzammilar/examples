USE demo;
SELECT @@global.tidb_txn_mode AS default_txn_mode;

-- Pessimistic (the default): SELECT ... FOR UPDATE takes locks in TiKV right away.
BEGIN PESSIMISTIC;
SELECT id, balance FROM accounts WHERE id IN (1, 2) FOR UPDATE;
UPDATE accounts SET balance = balance - 30 WHERE id = 1;
UPDATE accounts SET balance = balance + 30 WHERE id = 2;
COMMIT;

-- Optimistic: no locks until COMMIT, when two-phase commit (prewrite + commit)
-- checks for write conflicts on every key.
BEGIN OPTIMISTIC;
UPDATE accounts SET balance = balance - 20 WHERE id = 2;
UPDATE accounts SET balance = balance + 20 WHERE id = 3;
COMMIT;

-- A cross-region transaction: an order row (in some orders region) and an account
-- row (in the accounts region) change atomically.
BEGIN;
INSERT INTO orders (customer_id, amount, created_at) SELECT id, 10.00, NOW() FROM customers WHERE name = 'customer-1';
UPDATE accounts SET balance = balance - 10 WHERE id = 3;
COMMIT;

-- ROLLBACK leaves nothing behind.
BEGIN;
UPDATE accounts SET balance = 0;
ROLLBACK;

SELECT id, owner, balance FROM accounts ORDER BY id;
SELECT SUM(balance) AS total FROM accounts;
