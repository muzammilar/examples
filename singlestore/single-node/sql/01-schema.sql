-- Three table flavours in one database:
--   REFERENCE: small, copied in full to every node, so joins with it never move data
--   ROWSTORE:  in-memory, row-oriented, lock-free skiplist indexes (OLTP point reads/writes)
--   default:   columnstore ("universal storage"), on disk, compressed; SORT KEY orders rows inside
--              each segment so min/max metadata can skip blocks and segments
-- SHARD KEY picks the column hashed to choose a partition; tables sharded on the join key are
-- joined locally on each leaf (a "colocated" join).
DROP DATABASE IF EXISTS demo;
CREATE DATABASE demo;
USE demo;

CREATE REFERENCE TABLE digits (n INT PRIMARY KEY);

CREATE REFERENCE TABLE regions (
  region_id INT PRIMARY KEY,
  name VARCHAR(20) NOT NULL
);

CREATE ROWSTORE TABLE customers (
  customer_id INT NOT NULL,
  region_id INT NOT NULL,
  email VARCHAR(64) NOT NULL,
  tier ENUM('free', 'pro', 'enterprise') NOT NULL,
  PRIMARY KEY (customer_id),
  SHARD KEY (customer_id)
);

CREATE TABLE orders (
  order_id BIGINT NOT NULL,
  customer_id INT NOT NULL,
  amount DECIMAL(10, 2) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  attrs JSON NOT NULL,
  SORT KEY (created_at),
  SHARD KEY (customer_id),
  KEY (order_id) USING HASH
);

SELECT TABLE_NAME, STORAGE_TYPE FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'demo' ORDER BY TABLE_NAME;
