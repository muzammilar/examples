-- analytic queries over the 3M orders: join + GROUP BY, percentiles, window functions
\timing on
SELECT c.region, c.segment, count(*) AS orders, round(sum(o.amount)) AS revenue,
       avg(o.amount)::numeric(10,2) AS avg_order
FROM orders o JOIN customers c ON c.id = o.customer_id
WHERE o.status <> 'returned'
GROUP BY c.region, c.segment
ORDER BY revenue DESC
LIMIT 6;

SELECT status,
       (percentile_cont(0.5)  WITHIN GROUP (ORDER BY amount))::numeric(10,2) AS p50,
       (percentile_cont(0.99) WITHIN GROUP (ORDER BY amount))::numeric(10,2) AS p99
FROM orders GROUP BY status ORDER BY status;

-- monthly revenue with running total and month-over-month change
SELECT month, revenue,
       sum(revenue) OVER (ORDER BY month) AS running_total,
       revenue - lag(revenue) OVER (ORDER BY month) AS mom_change
FROM (SELECT date_trunc('month', ordered_at) AS month, round(sum(amount)) AS revenue
      FROM orders GROUP BY 1) m
ORDER BY month
LIMIT 6;

-- top 2 customers per region by spend
SELECT region, customer_id, spend, rnk
FROM (SELECT c.region, o.customer_id, sum(o.amount) AS spend,
             rank() OVER (PARTITION BY c.region ORDER BY sum(o.amount) DESC) AS rnk
      FROM orders o JOIN customers c ON c.id = o.customer_id
      GROUP BY c.region, o.customer_id) t
WHERE rnk <= 2
ORDER BY region, rnk;

EXPLAIN
SELECT c.region, count(*), sum(o.amount)
FROM orders o JOIN customers c ON c.id = o.customer_id
WHERE o.ordered_at >= timestamp '2025-06-01'
GROUP BY c.region;

EXPLAIN ANALYZE
SELECT c.region, count(*), sum(o.amount)
FROM orders o JOIN customers c ON c.id = o.customer_id
WHERE o.ordered_at >= timestamp '2025-06-01'
GROUP BY c.region;
