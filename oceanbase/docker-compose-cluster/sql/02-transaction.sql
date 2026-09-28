USE demo;
-- A transaction that writes to two HASH partitions of accounts and one RANGE partition
-- of orders. OceanBase commits it atomically across every tablet it touched. Every user
-- tablet is in log stream 1001 (sql/04: one user log stream, since PRIMARY_ZONE is a
-- priority list), so it commits one-phase; the commit returns once the redo is persisted
-- by a majority (2 of 3) of the Paxos replicas in zone1, zone2 and zone3.
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
