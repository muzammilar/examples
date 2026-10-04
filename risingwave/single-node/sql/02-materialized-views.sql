-- A materialized view is a streaming job: created once, then updated incrementally on every
-- change to its inputs. Join + aggregate:
CREATE MATERIALIZED VIEW revenue_by_region AS
SELECT c.region, count(*) AS orders, sum(o.amount) AS revenue
FROM orders o JOIN customers c ON o.customer_id = c.customer_id
WHERE o.status = 'paid'
GROUP BY c.region;

-- MV on a source: page views per customer, joined to a table (stream-table join).
CREATE MATERIALIZED VIEW views_per_customer AS
SELECT c.name, count(*) AS views
FROM page_views v JOIN customers c ON v.customer_id = c.customer_id
GROUP BY c.name;

SELECT * FROM revenue_by_region ORDER BY region;

-- Updates and deletes flow through: retractions remove the old row's contribution.
UPDATE orders SET status = 'paid' WHERE order_id = 4;      -- eu +99.99
UPDATE customers SET region = 'us' WHERE customer_id = 3;  -- carol's 7.25 moves eu -> us
DELETE FROM orders WHERE order_id = 2;                      -- us -25.50
FLUSH;
SELECT * FROM revenue_by_region ORDER BY region;

-- Fails (division by zero) unless the MV equals the same query run from scratch.
SELECT 1 / (CASE WHEN (SELECT sum(revenue) FROM revenue_by_region) = 117.24 THEN 1 ELSE 0 END) AS mv_matches;

-- The datagen source keeps feeding views_per_customer.
SELECT * FROM views_per_customer ORDER BY name;
