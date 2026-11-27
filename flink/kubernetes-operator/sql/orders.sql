SET 'execution.runtime-mode' = 'streaming';
SET 'table.dml-sync' = 'true';
SET 'execution.checkpointing.interval' = '2 s';
SET 'execution.checkpointing.dir' = 's3://flink-lab/checkpoints';
SET 'restart-strategy.type' = 'fixed-delay';
SET 'restart-strategy.fixed-delay.attempts' = '10';
SET 'restart-strategy.fixed-delay.delay' = '10 s';
SET 'parallelism.default' = '2';
CREATE TABLE orders (
    order_id BIGINT,
    customer_id AS MOD(order_id, 10000),
    amount_cents AS 100 + MOD(order_id, 1000)
) WITH (
    'connector' = 'datagen',
    'number-of-rows' = '1000000',
    'fields.order_id.kind' = 'sequence',
    'fields.order_id.start' = '0',
    'fields.order_id.end' = '999999'
);
CREATE TABLE output_orders (
    order_id BIGINT,
    customer_id BIGINT,
    amount_cents BIGINT
) WITH (
    'connector' = 'filesystem',
    'path' = 's3://flink-lab/orders',
    'format' = 'csv'
);
INSERT INTO output_orders SELECT order_id, customer_id, amount_cents FROM orders;
