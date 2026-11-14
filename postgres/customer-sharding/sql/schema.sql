CREATE TABLE IF NOT EXISTS customers (
 customer_id bigint PRIMARY KEY, name text NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
 customer_id bigint NOT NULL, op_id bigint NOT NULL, amount bigint NOT NULL,
 PRIMARY KEY(customer_id, op_id)
);
