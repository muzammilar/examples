SET 'execution.runtime-mode' = 'streaming';
SET 'execution.attached' = 'false';
SET 'pipeline.name' = 'orders-cdc-join-two-sinks';
SET 'parallelism.default' = '2';
SET 'execution.checkpointing.interval' = '5s';
SET 'execution.checkpointing.timeout' = '120s';
SET 'execution.checkpointing.min-pause' = '1s';
SET 'execution.checkpointing.max-concurrent-checkpoints' = '1';
SET 'execution.checkpointing.externalized-checkpoint-retention' = 'RETAIN_ON_CANCELLATION';
SET 'table.exec.source.cdc-events-duplicate' = 'true';
CREATE TABLE source_orders (
 id BIGINT NOT NULL, customer_id BIGINT, amount_cents BIGINT,
 revision BIGINT, cancelled BOOLEAN, event_second BIGINT,
 PRIMARY KEY (id) NOT ENFORCED
) WITH (
 'connector'='kafka', 'topic'='cdc.public.orders',
 'properties.bootstrap.servers'='broker1:9092,broker2:9092,broker3:9092',
 'properties.group.id'='pipeline-orders', 'scan.startup.mode'='earliest-offset',
 'format'='debezium-json', 'debezium-json.schema-include'='false'
);
CREATE TABLE source_customers (
 id BIGINT NOT NULL, region STRING, revision BIGINT,
 PRIMARY KEY (id) NOT ENFORCED
) WITH (
 'connector'='kafka', 'topic'='cdc.public.customers',
 'properties.bootstrap.servers'='broker1:9092,broker2:9092,broker3:9092',
 'properties.group.id'='pipeline-customers', 'scan.startup.mode'='earliest-offset',
 'format'='debezium-json', 'debezium-json.schema-include'='false'
);
CREATE VIEW enriched_orders AS
 SELECT o.id AS order_id,o.customer_id,o.amount_cents,c.region,
 o.revision AS order_revision,c.revision AS customer_revision,o.event_second,
 o.revision*CAST(1000000 AS BIGINT)+c.revision AS version
 FROM source_orders o JOIN source_customers c ON o.customer_id=c.id
 WHERE NOT o.cancelled;
CREATE TABLE checkpointed (
 order_id BIGINT NOT NULL,customer_id BIGINT,amount_cents BIGINT,region STRING,
 order_revision BIGINT,customer_revision BIGINT,event_second BIGINT,version BIGINT,
 PRIMARY KEY (order_id) NOT ENFORCED
) WITH (
 'connector'='doris', 'fenodes'='fe1:8030,fe2:8030,fe3:8030',
 'jdbc-url'='jdbc:mysql://fe1:9030', 'table.identifier'='pipeline.checkpointed',
 'username'='pipeline', 'password'='local-doris-only', 'sink.label-prefix'='orders_checkpointed',
 'sink.enable-delete'='true', 'sink.properties.format'='json',
 'sink.properties.read_json_by_line'='true', 'sink.enable-2pc'='true', 'sink.enable.batch-mode'='false'
);
CREATE TABLE asynchronous (
 order_id BIGINT NOT NULL,customer_id BIGINT,amount_cents BIGINT,region STRING,
 order_revision BIGINT,customer_revision BIGINT,event_second BIGINT,version BIGINT,
 PRIMARY KEY (order_id) NOT ENFORCED
) WITH (
 'connector'='doris', 'fenodes'='fe1:8030,fe2:8030,fe3:8030',
 'jdbc-url'='jdbc:mysql://fe1:9030', 'table.identifier'='pipeline.asynchronous',
 'username'='pipeline', 'password'='local-doris-only', 'sink.label-prefix'='orders_asynchronous',
 'sink.enable-delete'='true', 'sink.properties.format'='json',
 'sink.properties.read_json_by_line'='true', 'sink.enable-2pc'='false', 'sink.enable.batch-mode'='true', 'sink.buffer-flush.max-rows'='10000', 'sink.buffer-flush.interval'='1s'
);
EXECUTE STATEMENT SET BEGIN
INSERT INTO checkpointed SELECT * FROM enriched_orders ON CONFLICT DO DEDUPLICATE;
INSERT INTO asynchronous SELECT * FROM enriched_orders ON CONFLICT DO DEDUPLICATE;
END;
