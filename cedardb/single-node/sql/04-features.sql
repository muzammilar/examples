-- CSV export and querying the file in place (csvview), pgvector-compatible vector type
\timing on
SET client_min_messages = warning;
COPY (SELECT id, region, segment FROM customers WHERE id <= 1000) TO '/tmp/customers.csv' CSV HEADER;
SELECT region, count(*)
FROM csvview('/tmp/customers.csv', 'delimiter ",", header', 'id integer, region text, segment text')
GROUP BY region ORDER BY region;

DROP TABLE IF EXISTS items;
CREATE TABLE items (id integer PRIMARY KEY, name text, embedding vector(3));
INSERT INTO items VALUES
  (1, 'cat', '[1,2,3]'), (2, 'dog', '[1,3,4]'), (3, 'car', '[9,1,0]'), (4, 'truck', '[8,2,1]');
SELECT name, embedding <-> '[1,2,4]' AS l2, embedding <=> '[1,2,4]' AS cosine
FROM items ORDER BY embedding <-> '[1,2,4]' LIMIT 3;
