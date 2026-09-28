-- The aggregator sends each query to every partition and merges the partial results.
USE demo;

-- customers and orders are both sharded on customer_id: the join runs locally in each
-- partition (no Repartition/Broadcast in the plan); regions is a reference table, present everywhere
EXPLAIN
SELECT r.name AS region, c.tier, COUNT(*) AS orders, SUM(o.amount) AS revenue
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN regions r ON r.region_id = c.region_id
GROUP BY r.name, c.tier;

SELECT r.name AS region, c.tier, COUNT(*) AS orders, SUM(o.amount) AS revenue
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN regions r ON r.region_id = c.region_id
GROUP BY r.name, c.tier
ORDER BY region, tier;

-- joining on a column that is not the shard key forces data movement: one side is
-- repartitioned (reshuffled by the join key) or broadcast to all partitions
EXPLAIN
SELECT COUNT(*) FROM orders o JOIN customers c ON c.customer_id = o.order_id % 50000;

-- PROFILE runs the query and records per-operator row counts and timings
PROFILE
SELECT c.tier, COUNT(DISTINCT o.customer_id) AS buyers, ROUND(AVG(o.amount), 2) AS avg_amount
FROM orders o JOIN customers c ON c.customer_id = o.customer_id
WHERE o.created_at >= '2026-06-01' AND o.created_at < '2026-07-01'
GROUP BY c.tier ORDER BY c.tier;
SHOW PROFILE;

-- window function over the distributed result
SELECT customer_id, orders, spent, RANK() OVER (ORDER BY spent DESC) AS rnk
FROM (SELECT customer_id, COUNT(*) AS orders, SUM(amount) AS spent FROM orders GROUP BY customer_id) t
ORDER BY rnk LIMIT 5;
