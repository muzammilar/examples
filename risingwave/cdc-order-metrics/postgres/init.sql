-- OLTP schema in Postgres (database `shop`). RisingWave reads it through the postgres-cdc
-- connector (logical replication, publication + slot created by RisingWave).
CREATE TABLE customers (
    customer_id INT PRIMARY KEY,
    name        TEXT NOT NULL,
    region      TEXT NOT NULL
);
CREATE TABLE products (
    product_id  INT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL
);
CREATE TABLE orders (
    order_id    BIGINT PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers,
    product_id  INT NOT NULL REFERENCES products,
    qty         INT NOT NULL,
    price_cents BIGINT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX orders_customer ON orders (customer_id);
CREATE INDEX orders_product ON orders (product_id);
