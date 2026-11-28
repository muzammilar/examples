CREATE TABLE customers (
  id BIGINT PRIMARY KEY, region VARCHAR(32) NOT NULL,
  discount_bps INT NOT NULL CHECK (discount_bps BETWEEN 0 AND 10000),
  revision BIGINT NOT NULL CHECK (revision BETWEEN 1 AND 999999)
);
CREATE TABLE orders (
  id BIGINT PRIMARY KEY, customer_id BIGINT NOT NULL,
  amount_cents BIGINT NOT NULL, cost_cents BIGINT NOT NULL,
  revision BIGINT NOT NULL CHECK (revision BETWEEN 1 AND 1000000),
  cancelled TINYINT NOT NULL CHECK (cancelled IN (0,1)), event_second BIGINT NOT NULL,
  FOREIGN KEY (customer_id) REFERENCES customers(id)
);
CREATE TABLE receipts (
  id VARCHAR(64) PRIMARY KEY, operation VARCHAR(32) NOT NULL,
  first_id BIGINT NOT NULL, last_id BIGINT NOT NULL, affected BIGINT NOT NULL
);
CREATE TABLE debezium_signal (
  id VARCHAR(64) PRIMARY KEY, type VARCHAR(32) NOT NULL, data TEXT NOT NULL
);
CREATE USER 'app'@'%' IDENTIFIED BY 'local-app-only';
GRANT SELECT, INSERT, UPDATE, DELETE ON orders.customers TO 'app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON orders.orders TO 'app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON orders.receipts TO 'app'@'%';
GRANT SELECT, INSERT, UPDATE ON orders.debezium_signal TO 'app'@'%';
CREATE USER 'cdc'@'%' IDENTIFIED BY 'local-cdc-only';
GRANT RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'cdc'@'%';
GRANT SELECT, LOCK TABLES ON orders.* TO 'cdc'@'%';
GRANT INSERT, UPDATE, DELETE ON orders.debezium_signal TO 'cdc'@'%';
