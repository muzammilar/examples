CREATE TABLE IF NOT EXISTS customers (
 customer_id BIGINT NOT NULL, name VARCHAR(100) NOT NULL,
 PRIMARY KEY(customer_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS orders (
 customer_id BIGINT NOT NULL, op_id BIGINT NOT NULL, amount BIGINT NOT NULL,
 PRIMARY KEY(customer_id,op_id)
) ENGINE=InnoDB;
