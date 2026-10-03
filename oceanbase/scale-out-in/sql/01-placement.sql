-- A 12-partition table: where its partitions (tablets) live, per log stream and leader.
-- With UNIT_NUM = 1 every user tablet is in one log stream (LS 1001, leader on ob1);
-- after `make scale-out` (UNIT_NUM = 2) half of them are transferred to a second log stream
-- whose replicas sit on ob4..ob6.
CREATE DATABASE IF NOT EXISTS demo;
CREATE TABLE IF NOT EXISTS demo.orders (
  id BIGINT PRIMARY KEY, customer BIGINT, amount DECIMAL(10, 2)
) PARTITION BY HASH(id) PARTITIONS 12;
INSERT IGNORE INTO demo.orders
  WITH RECURSIVE s(n) AS (SELECT 0 UNION ALL SELECT n + 1 FROM s WHERE n < 999)
  SELECT n, n % 100, n % 1000 FROM s;

SELECT LS_ID, SVR_IP AS leader, COUNT(*) AS partitions, GROUP_CONCAT(PARTITION_NAME ORDER BY TABLET_ID) AS names
  FROM oceanbase.DBA_OB_TABLE_LOCATIONS
 WHERE DATABASE_NAME = 'demo' AND TABLE_NAME = 'orders' AND ROLE = 'LEADER'
 GROUP BY LS_ID, SVR_IP ORDER BY LS_ID;

SELECT LS_ID, STATUS, UNIT_LIST FROM oceanbase.DBA_OB_LS ORDER BY LS_ID;
