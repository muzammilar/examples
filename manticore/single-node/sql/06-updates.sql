-- RT table changes: UPDATE changes attributes in place; REPLACE rewrites a document
-- (needed for text fields); DELETE marks rows deleted (purged on merge/OPTIMIZE).
UPDATE products SET price = 99.0, tags = (2, 3) WHERE id = 1;
REPLACE INTO products (id, title, description, category, brand, price, rating, stock, tags, added, embedding)
  VALUES (8, 'Noise cancelling headphones (2026)', 'Over-ear headphones for travel, the office and flights', 'electronics', 'Sony', 379.0, 4.8, 25, (4), 1791000000, (0.0, 0.1, 1.0, 0.0));
DELETE FROM products WHERE id = 15;
SELECT id, title, price, tags FROM products WHERE id IN (1, 8, 15);
SELECT id, title FROM products WHERE MATCH('flights');

-- transactions: INSERT/REPLACE/DELETE on one RT table applied atomically on COMMIT
-- (UPDATE takes constants only: `SET stock = stock - 1` is a syntax error)
BEGIN;
INSERT INTO products (id, title, description, category, brand, price, rating, stock, tags, added, embedding)
  VALUES (17, 'Pour-over coffee kettle', 'Gooseneck kettle for pour-over coffee', 'kitchen', 'Fellow', 165.0, 4.7, 14, (4), 1791100000, (0.0, 0.0, 0.2, 1.0));
DELETE FROM products WHERE id = 14;
COMMIT;
SELECT id, title FROM products WHERE id IN (14, 17);
SELECT COUNT(*) AS products FROM products;
