USE demo;
-- Partition pruning: the equality on the HASH key and the date range on the RANGE key
-- each leave a single partition in the plan's `partitions(...)`. Pruning is partition-level
-- only: the orders plan is still a TABLE FULL SCAN within that partition (no index on created).
EXPLAIN SELECT * FROM accounts WHERE id = 42;
EXPLAIN SELECT SUM(amount) FROM orders WHERE created BETWEEN '2026-05-01' AND '2026-05-31';

-- Parallel execution: PX COORDINATOR / EXCHANGE operators and a degree of parallelism of 4.
EXPLAIN SELECT /*+ PARALLEL(4) */ account_id, SUM(amount)
  FROM orders GROUP BY account_id ORDER BY 2 DESC LIMIT 5;

SELECT /*+ PARALLEL(4) */ account_id, SUM(amount) AS total
  FROM orders GROUP BY account_id ORDER BY total DESC, account_id LIMIT 5;
