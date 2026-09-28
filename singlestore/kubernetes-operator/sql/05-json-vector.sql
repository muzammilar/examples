USE demo;

-- JSON: ::$key extracts a string, ::%key a number; JSON columns are stored columnar per key
SELECT attrs::$channel AS channel, COUNT(*) AS orders, SUM(attrs::%items) AS items,
       SUM(attrs::$coupon = 'SPRING') AS with_coupon
FROM orders GROUP BY channel ORDER BY channel;

SELECT JSON_AGG(attrs) AS sample FROM (SELECT attrs FROM orders WHERE order_id < 3 ORDER BY order_id) t;

-- VECTOR type with DOT_PRODUCT (<*>) and EUCLIDEAN_DISTANCE (<->)
SET vector_type_project_format = JSON;

CREATE TABLE IF NOT EXISTS products (
  id INT NOT NULL,
  name VARCHAR(32) NOT NULL,
  embedding VECTOR(4) NOT NULL,
  SHARD KEY (id)
);
TRUNCATE products;
INSERT INTO products VALUES
  (1, 'running shoes', '[0.9, 0.1, 0.0, 0.1]'),
  (2, 'trail shoes',   '[0.8, 0.3, 0.1, 0.0]'),
  (3, 'rain jacket',   '[0.1, 0.9, 0.2, 0.0]'),
  (4, 'coffee beans',  '[0.0, 0.0, 0.1, 0.9]'),
  (5, 'espresso cup',  '[0.1, 0.0, 0.2, 0.8]');

SET @q = '[1.0, 0.2, 0.0, 0.0]' :> VECTOR(4);
SELECT id, name, embedding, ROUND(embedding <*> @q, 3) AS dot, ROUND(embedding <-> @q, 3) AS dist
FROM products ORDER BY dot DESC LIMIT 3;
