-- JSONB documents: nested reads with -> (JSONB) and ->> (text), the index on doc->>'brand'
-- (ycql/schema/indexes.cql), and updates of single attributes that leave the rest alone.
USE demo;
INSERT INTO products (id, doc) VALUES (1, '{"name": "lamp", "brand": "acme", "price": 25, "dims": {"w": 10, "h": 40}, "tags": ["new", "sale"]}');
INSERT INTO products (id, doc) VALUES (2, '{"name": "desk", "brand": "acme", "price": 180, "dims": {"w": 120, "h": 75}, "tags": ["office"]}');
INSERT INTO products (id, doc) VALUES (3, '{"name": "chair", "brand": "zeta", "price": 60, "dims": {"w": 45, "h": 90}, "tags": []}');

SELECT id, doc->>'name' AS name, doc->'dims'->>'w' AS width, doc->'tags'->>0 AS first_tag FROM products;

-- the attribute index serves the brand lookup
EXPLAIN SELECT id, doc->>'name' FROM products WHERE doc->>'brand' = 'acme';
SELECT id, doc->>'name' FROM products WHERE doc->>'brand' = 'acme';

-- filters on other attributes scan the table
SELECT id, doc->>'name' FROM products WHERE CAST(doc->>'price' AS integer) > 50 ALLOW FILTERING;
SELECT id FROM products WHERE doc->'tags'->>0 = 'new' ALLOW FILTERING;

-- partial updates: the path ends in ->, the new value is JSON (strings quoted inside the literal)
UPDATE products SET doc->'dims'->'w' = '12' WHERE id = 1;
UPDATE products SET doc->'name' = '"desk lamp"' WHERE id = 1;
SELECT doc FROM products WHERE id = 1;
