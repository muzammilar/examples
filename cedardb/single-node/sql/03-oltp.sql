-- OLTP on the same tables (HTAP): point lookups, a committed and a rolled-back transaction
\timing on
SELECT id, customer_id, status, amount FROM orders WHERE id = 42;

BEGIN;
UPDATE orders SET status = 'shipped' WHERE id = 42;
INSERT INTO orders VALUES (3000001, 42, now(), 'placed', 19.99);
UPDATE customers SET segment = 'enterprise' WHERE id = 42;
COMMIT;

BEGIN;
DELETE FROM orders WHERE customer_id = 42;
SELECT count(*) AS orders_of_42_inside_tx FROM orders WHERE customer_id = 42;
ROLLBACK;

SELECT count(*) AS orders_of_42_after_rollback FROM orders WHERE customer_id = 42;
SELECT id, status, amount FROM orders WHERE id IN (42, 3000001) ORDER BY id;

-- bulk update of 1/4 of the table, then the analytics see it immediately
UPDATE orders SET amount = amount * 1.1 WHERE status = 'placed';
SELECT status, count(*), round(sum(amount)) FROM orders GROUP BY status ORDER BY status;
