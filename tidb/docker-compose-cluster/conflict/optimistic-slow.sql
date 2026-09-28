-- session A: optimistic, updates alice, waits, commits after B -> write conflict
USE demo;
BEGIN OPTIMISTIC;
UPDATE accounts SET balance = balance + 1 WHERE id = 1;
DO SLEEP(3);
COMMIT;
