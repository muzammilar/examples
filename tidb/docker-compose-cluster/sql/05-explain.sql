USE demo;
-- cop[tikv] operators run inside TiKV (one coprocessor task per region, in
-- parallel); root operators run in TiDB and merge the partial results.
EXPLAIN ANALYZE SELECT COUNT(*), SUM(amount) FROM orders;
EXPLAIN ANALYZE
SELECT c.country, COUNT(*) AS orders, SUM(o.amount) AS revenue
FROM orders o JOIN customers c ON c.id = o.customer_id
WHERE o.amount > 250
GROUP BY c.country ORDER BY revenue DESC;
SELECT c.country, COUNT(*) AS orders, SUM(o.amount) AS revenue
FROM orders o JOIN customers c ON c.id = o.customer_id
WHERE o.amount > 250
GROUP BY c.country ORDER BY revenue DESC;
