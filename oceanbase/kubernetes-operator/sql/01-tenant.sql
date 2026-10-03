-- MySQL-mode user tenant `test`, created by the OBTenant resource.
SELECT ob_version() AS version, DATABASE() AS db, CURRENT_USER() AS user;

CREATE DATABASE IF NOT EXISTS demo;
CREATE TABLE IF NOT EXISTS demo.orders (
  id BIGINT PRIMARY KEY, customer VARCHAR(32), amount DECIMAL(10, 2)
) PARTITION BY HASH(id) PARTITIONS 4;
REPLACE INTO demo.orders VALUES (1, 'alice', 10.50), (2, 'bob', 7.25), (3, 'carol', 99.00), (4, 'dave', 1.00);

BEGIN;
UPDATE demo.orders SET amount = amount + 1 WHERE id IN (1, 2);
COMMIT;
SELECT * FROM demo.orders ORDER BY id;

-- the four partitions live in the tenant's one user log stream, on the one observer
SELECT TABLE_NAME, PARTITION_NAME, TABLET_ID, LS_ID, SVR_IP, ROLE
  FROM oceanbase.DBA_OB_TABLE_LOCATIONS
 WHERE DATABASE_NAME = 'demo' ORDER BY TABLET_ID;
