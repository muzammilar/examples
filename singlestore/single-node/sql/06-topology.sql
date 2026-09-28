-- Cluster layout: aggregators (query routing, planning, merging), leaves (data), and the
-- database partitions spread over the leaves.
USE demo;

SHOW AGGREGATORS;
SHOW LEAVES;
SHOW PARTITIONS ON demo;

SELECT HOST, PORT, ROLE, COUNT(*) AS partitions
FROM information_schema.DISTRIBUTED_PARTITIONS
WHERE DATABASE_NAME = 'demo' GROUP BY HOST, PORT, ROLE ORDER BY HOST, PORT, ROLE;

-- rows per partition: hashing customer_id spreads rows evenly (orders: rows in on-disk
-- segments; the row UPDATEd in 04 still sits in the in-memory segment)
SELECT 'customers' AS table_name, ORDINAL AS partition_id, `ROWS`
FROM information_schema.TABLE_STATISTICS
WHERE DATABASE_NAME = 'demo' AND TABLE_NAME = 'customers' AND PARTITION_TYPE = 'Master'
UNION ALL
SELECT 'orders', `PARTITION`, SUM(ROWS_COUNT)
FROM information_schema.COLUMNAR_SEGMENTS
WHERE DATABASE_NAME = 'demo' AND TABLE_NAME = 'orders' AND COLUMN_NAME = 'order_id'
GROUP BY `PARTITION`
ORDER BY 1, 2;
