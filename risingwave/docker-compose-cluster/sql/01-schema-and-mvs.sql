-- 200k orders over 1000 customers, a join + aggregate MV, and a check that the MV equals the
-- same query run as a batch query.
CREATE TABLE customers (customer_id INT PRIMARY KEY, region VARCHAR);
CREATE TABLE orders (order_id BIGINT PRIMARY KEY, customer_id INT, amount BIGINT);

CREATE MATERIALIZED VIEW revenue_by_region AS
SELECT c.region, count(*) AS orders, sum(o.amount) AS revenue
FROM orders o JOIN customers c ON o.customer_id = c.customer_id
GROUP BY c.region;

INSERT INTO customers SELECT g, 'r' || (g % 8) FROM generate_series(1, 1000) g;
INSERT INTO orders SELECT g, 1 + g % 1000, g % 97 FROM generate_series(1, 200000) g;
FLUSH;

SELECT * FROM revenue_by_region ORDER BY region;

-- Fails with a division by zero unless the MV equals the batch query.
SELECT 1 / (CASE WHEN (SELECT sum(revenue) FROM revenue_by_region) =
                      (SELECT sum(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.customer_id)
                  AND (SELECT sum(orders) FROM revenue_by_region) = 200000
             THEN 1 ELSE 0 END) AS mv_matches_batch;

-- Moving a customer to another region updates both regions' rows.
UPDATE customers SET region = 'moved' WHERE customer_id <= 10;
FLUSH;
SELECT * FROM revenue_by_region WHERE region = 'moved';
