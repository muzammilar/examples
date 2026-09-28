-- session B: optimistic, updates alice and commits first -> wins
USE demo;
BEGIN OPTIMISTIC;
UPDATE accounts SET balance = balance + 5 WHERE id = 1;
COMMIT;
