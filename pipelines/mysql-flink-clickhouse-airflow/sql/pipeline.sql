SET 'execution.runtime-mode' = 'streaming';
SET 'execution.attached' = 'false';
SET 'pipeline.name' = 'mysql-current-margin';
SET 'parallelism.default' = '2';
SET 'state.backend.type' = 'rocksdb';
SET 'state.backend.incremental' = 'true';
SET 'state.checkpoints.dir' = 'file:///data/checkpoints';
SET 'state.checkpoints.num-retained' = '2';
SET 'execution.checkpointing.interval' = '5s';
SET 'execution.checkpointing.timeout' = '120s';
SET 'execution.checkpointing.min-pause' = '1s';
SET 'execution.checkpointing.max-concurrent-checkpoints' = '1';
SET 'execution.checkpointing.externalized-checkpoint-retention' = 'RETAIN_ON_CANCELLATION';
SET 'table.exec.source.cdc-events-duplicate' = 'true';
CREATE TABLE source_orders (
  id BIGINT NOT NULL, customer_id BIGINT, amount_cents BIGINT, cost_cents BIGINT,
  shipping_cents BIGINT, revision BIGINT, cancelled TINYINT, event_second BIGINT,
  PRIMARY KEY (id) NOT ENFORCED
) WITH (
  'connector'='kafka', 'topic'='cdc.orders.orders',
  'properties.bootstrap.servers'='broker1:9092,broker2:9092,broker3:9092',
  'properties.group.id'='margins-source-orders', 'scan.startup.mode'='earliest-offset',
  'properties.allow.auto.create.topics'='false',
  'format'='debezium-json', 'debezium-json.schema-include'='false'
);
CREATE TABLE source_customers (
  id BIGINT NOT NULL, region STRING, discount_bps INT, revision BIGINT,
  PRIMARY KEY (id) NOT ENFORCED
) WITH (
  'connector'='kafka', 'topic'='cdc.orders.customers',
  'properties.bootstrap.servers'='broker1:9092,broker2:9092,broker3:9092',
  'properties.group.id'='margins-source-customers', 'scan.startup.mode'='earliest-offset',
  'properties.allow.auto.create.topics'='false',
  'format'='debezium-json', 'debezium-json.schema-include'='false'
);
CREATE TABLE enriched_margins (
  order_id BIGINT NOT NULL, customer_id BIGINT, amount_cents BIGINT,
  cost_cents BIGINT, shipping_cents BIGINT, region STRING, discount_bps INT,
  margin_cents BIGINT, order_revision BIGINT, customer_revision BIGINT,
  event_second BIGINT, PRIMARY KEY (order_id) NOT ENFORCED
) WITH (
  'connector'='kafka', 'topic'='margins.enriched',
  'properties.bootstrap.servers'='broker1:9092,broker2:9092,broker3:9092',
  'properties.allow.auto.create.topics'='false',
  'key.format'='json', 'key.fields'='order_id',
  'value.format'='debezium-json', 'value.fields-include'='ALL',
  'sink.partitioner'='default', 'sink.delivery-guarantee'='exactly-once',
  'sink.transactional-id-prefix'='mysql-margins-output',
  'properties.transaction.timeout.ms'='300000',
  'properties.acks'='all', 'properties.compression.type'='lz4'
);
INSERT INTO enriched_margins
SELECT o.id,o.customer_id,o.amount_cents,o.cost_cents,COALESCE(o.shipping_cents,0),
  c.region,c.discount_bps,
  o.amount_cents-o.cost_cents-COALESCE(o.shipping_cents,0)
    -CAST(FLOOR(o.amount_cents*CAST(c.discount_bps AS DECIMAL(18,0))/10000) AS BIGINT),
  o.revision,c.revision,o.event_second
FROM source_orders o JOIN source_customers c ON o.customer_id=c.id
WHERE o.cancelled=0;
