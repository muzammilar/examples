-- Tables take INSERT/UPDATE/DELETE (DML) and keep their own state; a source only reads from a
-- connector. datagen is built in, so no Kafka is needed for the walkthrough.
CREATE TABLE customers (
    customer_id INT PRIMARY KEY,
    name        VARCHAR,
    region      VARCHAR
);

CREATE TABLE orders (
    order_id    BIGINT PRIMARY KEY,
    customer_id INT,
    amount      NUMERIC,
    status      VARCHAR,
    created_at  TIMESTAMP
);

-- An endless stream of page views, 200 rows/s, generated inside RisingWave.
CREATE SOURCE page_views (
    customer_id INT,
    url         VARCHAR,
    viewed_at   TIMESTAMP
) WITH (
    connector = 'datagen',
    fields.customer_id.kind = 'random',
    fields.customer_id.min = '1',
    fields.customer_id.max = '3',
    fields.url.length = '8',
    fields.viewed_at.kind = 'random',
    fields.viewed_at.max_past = '10s',
    datagen.rows.per.second = '200'
) FORMAT PLAIN ENCODE JSON;

INSERT INTO customers VALUES (1, 'alice', 'eu'), (2, 'bob', 'us'), (3, 'carol', 'eu');
INSERT INTO orders VALUES
    (1, 1, 10.00, 'paid', '2026-01-01 10:00:00'),
    (2, 2, 25.50, 'paid', '2026-01-01 10:00:05'),
    (3, 3,  7.25, 'paid', '2026-01-01 10:00:09'),
    (4, 1, 99.99, 'pending', '2026-01-01 10:00:12');

-- DML returns before the data reaches the tables' state; FLUSH waits for the next checkpoint.
FLUSH;
SELECT count(*) AS orders FROM orders;
