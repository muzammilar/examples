USE demo;
-- A transaction that writes to two HASH partitions of accounts and one RANGE partition
-- of orders. OceanBase commits it atomically across every tablet it touched; when those
-- tablets' leaders live in different log streams / servers, commit is two-phase. Here
-- every tablet is in log stream 1001 on the one observer (sql/04), so it commits one-phase.
BEGIN;
UPDATE accounts SET balance = balance - 250 WHERE id = 1;
UPDATE accounts SET balance = balance + 250 WHERE id = 2;
INSERT INTO orders VALUES (20001, 1, '2026-09-27', 250.00);
-- Transaction id while it is still open.
SELECT IF(ob_transaction_id() > 0, 'open', 'none') AS tx;
COMMIT;

-- A failing transaction rolls back every partition.
BEGIN;
UPDATE accounts SET balance = balance - 999999 WHERE id = 3;
INSERT INTO orders VALUES (20002, 3, '2026-12-01', 999999.00);
ROLLBACK;

SELECT id, owner, balance FROM accounts WHERE id IN (1, 2, 3) ORDER BY id;
SELECT COUNT(*) AS orders_total, SUM(amount) AS amount_total FROM orders;
