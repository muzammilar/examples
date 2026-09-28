-- A distributed transaction across two tables (their tablets have different leaders):
-- both writes commit together or not at all.
CREATE KEYSPACE IF NOT EXISTS demo;
USE demo;
DROP TABLE IF EXISTS stock;
DROP TABLE IF EXISTS shipments;

CREATE TABLE stock (item text PRIMARY KEY, qty int) WITH transactions = {'enabled': true};
CREATE TABLE shipments (id int PRIMARY KEY, item text, qty int) WITH transactions = {'enabled': true};
INSERT INTO stock (item, qty) VALUES ('widget', 10);

-- Ship 3 widgets. (No IF clauses and no SELECTs are allowed inside the block.)
BEGIN TRANSACTION
  UPDATE stock SET qty = qty - 3 WHERE item = 'widget';
  INSERT INTO shipments (id, item, qty) VALUES (1, 'widget', 3);
END TRANSACTION;

-- Both rows carry the transaction's single commit time.
SELECT item, qty, writetime(qty) FROM stock;
SELECT id, item, qty, writetime(qty) FROM shipments;
