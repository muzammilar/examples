-- Sink into a RisingWave table: several streams can write into one table.
CREATE TABLE order_events (order_id BIGINT, kind VARCHAR, amount NUMERIC) APPEND ONLY;
CREATE SINK paid_orders_into_events INTO order_events AS
SELECT order_id, 'paid' AS kind, amount FROM orders WHERE status = 'paid' WITH (type = 'append-only', force_append_only = 'true');

INSERT INTO orders VALUES (5, 2, 42.00, 'paid', '2026-01-01 10:02:00');
FLUSH;
SELECT * FROM order_events ORDER BY order_id;

-- Native Postgres sink (since v2.2): upsert the MV into a table in the `sinkdb` Postgres container.
-- The target table is created there by `make up` (postgres/init.sql).
CREATE SINK revenue_to_postgres FROM revenue_by_region WITH (
    connector = 'postgres',
    host = 'postgres',
    port = '5432',
    user = 'postgres',
    password = 'postgres',
    database = 'sinkdb',
    table = 'revenue_by_region',
    type = 'upsert',
    primary_key = 'region'
);

INSERT INTO orders VALUES (6, 3, 3.01, 'paid', '2026-01-01 10:03:00');
FLUSH;
SELECT * FROM revenue_by_region ORDER BY region;
