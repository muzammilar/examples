-- session A: pessimistic, locks bob for 3 s
USE demo;
BEGIN PESSIMISTIC;
SELECT balance FROM accounts WHERE id = 2 FOR UPDATE;
DO SLEEP(3);
COMMIT;
