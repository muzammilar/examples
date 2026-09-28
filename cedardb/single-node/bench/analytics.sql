-- `make benchmark`: analytic queries over sql/01-generate.sql's 100k customers + 3M orders.
-- Results go to /dev/null; bench/run.sh runs this file RUNS times and report.py reads the
-- "Time:" line printed after each "query: <name>".
\timing on
\o /dev/null

\echo query: join-group-by
SELECT c.region, c.segment, count(*), sum(o.amount), avg(o.amount)
FROM orders o JOIN customers c ON c.id = o.customer_id
WHERE o.status <> 'returned'
GROUP BY c.region, c.segment;

-- TPC-H Q1-like: pricing summary over nearly the whole table
\echo query: q1-like-summary
SELECT status, count(*), sum(amount), avg(amount), min(amount), max(amount),
       sum(amount * 0.9) AS net, sum(amount * 0.9 * 1.08) AS gross
FROM orders
WHERE ordered_at < timestamp '2025-12-01'
GROUP BY status
ORDER BY status;

-- TPC-H Q6-like: selective range scan + sum
\echo query: q6-like-filter-sum
SELECT sum(amount * 0.05)
FROM orders
WHERE ordered_at >= timestamp '2025-03-01' AND ordered_at < timestamp '2025-06-01'
  AND amount BETWEEN 100 AND 500;

\echo query: percentiles
SELECT status,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY amount),
       percentile_cont(0.99) WITHIN GROUP (ORDER BY amount)
FROM orders GROUP BY status;

\echo query: count-distinct
SELECT date_trunc('month', ordered_at), count(DISTINCT customer_id)
FROM orders WHERE status = 'delivered' GROUP BY 1;

\echo query: window-running-total
SELECT month, revenue, sum(revenue) OVER (ORDER BY month),
       revenue - lag(revenue) OVER (ORDER BY month)
FROM (SELECT date_trunc('month', ordered_at) AS month, sum(amount) AS revenue
      FROM orders GROUP BY 1) m;

\echo query: top-n-per-group
SELECT region, customer_id, spend
FROM (SELECT c.region, o.customer_id, sum(o.amount) AS spend,
             rank() OVER (PARTITION BY c.region ORDER BY sum(o.amount) DESC) AS rnk
      FROM orders o JOIN customers c ON c.id = o.customer_id
      GROUP BY c.region, o.customer_id) t
WHERE rnk <= 10;
