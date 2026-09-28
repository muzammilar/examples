-- Covering and unique secondary indexes (tables and indexes: ycql/schema/indexes.cql).
USE demo;
INSERT INTO orders (id, customer, status, amount, note) VALUES (1, 'alice', 'shipped', 30, 'gift wrap');
INSERT INTO orders (id, customer, status, amount, note) VALUES (2, 'alice', 'open', 12, 'leave at door');
INSERT INTO orders (id, customer, status, amount, note) VALUES (3, 'bob', 'open', 99, 'call first');

-- Only key and INCLUDE columns: answered from the index alone (Index Only Scan) ...
EXPLAIN SELECT customer, status, amount FROM orders WHERE customer = 'alice';
SELECT customer, status, amount FROM orders WHERE customer = 'alice';
-- ... note is not in the index: the index finds the ids, then each row is read (Index Scan).
EXPLAIN SELECT id, note FROM orders WHERE customer = 'alice';
SELECT id, note FROM orders WHERE customer = 'alice';

-- Unique index: the duplicate insert is in ycql/errors/unique-violation.cql, which `make test`
-- expects to fail. A lookup by email reads one index entry (Index Only Key Lookup).
INSERT INTO customers (id, email, name) VALUES (1, 'alice@example.com', 'alice');
INSERT INTO customers (id, email, name) VALUES (2, 'bob@example.com', 'bob');
EXPLAIN SELECT id FROM customers WHERE email = 'bob@example.com';
SELECT * FROM customers WHERE email = 'bob@example.com';
