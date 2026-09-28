-- Columnstore: each partition stores orders as column segments (up to ~1M rows each) with
-- per-column compression and min/max metadata; SORT KEY (created_at) keeps rows ordered
-- inside a segment, so a created_at range filter skips the 4096-row blocks outside it.
USE demo;

SELECT COLUMN_NAME, COUNT(*) AS segments, SUM(ROWS_COUNT) AS rows_total,
       SUM(UNCOMPRESSED_SIZE) AS raw_bytes, SUM(COMPRESSED_SIZE) AS compressed_bytes,
       ROUND(SUM(UNCOMPRESSED_SIZE) / SUM(COMPRESSED_SIZE), 1) AS ratio
FROM information_schema.COLUMNAR_SEGMENTS
WHERE DATABASE_NAME = 'demo' AND TABLE_NAME = 'orders'
GROUP BY COLUMN_NAME ORDER BY COLUMN_NAME;

-- ColumnStoreScan reports number_of_blocks_tested_for_block_elim / ..._eliminated_for_block_elim
PROFILE
SELECT COUNT(*), SUM(amount) FROM orders
WHERE created_at >= '2026-03-01' AND created_at < '2026-03-08';
SHOW PROFILE;

-- point lookup through the hash index on the columnstore (universal storage: OLTP-style seek)
SELECT order_id, customer_id, amount, created_at, attrs FROM orders WHERE order_id = 424242;

-- update and delete work on columnstore too
UPDATE orders SET amount = amount + 1 WHERE order_id = 424242;
DELETE FROM orders WHERE order_id = 424243;
SELECT order_id, amount FROM orders WHERE order_id IN (424242, 424243);

-- rowstore: transactional point writes on the in-memory table
BEGIN;
UPDATE customers SET tier = 'enterprise' WHERE customer_id = 7;
SELECT customer_id, tier FROM customers WHERE customer_id = 7;
ROLLBACK;
SELECT customer_id, tier FROM customers WHERE customer_id = 7;
