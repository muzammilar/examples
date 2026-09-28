-- session B: pessimistic, waits at most 1 s for bob's lock -> lock wait timeout
USE demo;
SET SESSION innodb_lock_wait_timeout = 1;
BEGIN PESSIMISTIC;
SELECT balance FROM accounts WHERE id = 2 FOR UPDATE;
COMMIT;
