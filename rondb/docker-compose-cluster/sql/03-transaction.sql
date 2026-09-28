-- NDB transactions are ACID across data nodes (two-phase commit inside the cluster).
USE demo;

-- move 30 from alice (id 1) to bob (id 2) atomically
START TRANSACTION;
UPDATE accounts SET balance = balance - 30 WHERE id = 1;
UPDATE accounts SET balance = balance + 30 WHERE id = 2;
COMMIT;

-- rolled back: nothing changes
START TRANSACTION;
UPDATE accounts SET balance = 0 WHERE id IN (3, 4);
ROLLBACK;

SELECT id, owner, balance FROM accounts WHERE id <= 4 ORDER BY id;
SELECT SUM(balance) AS total FROM accounts;
