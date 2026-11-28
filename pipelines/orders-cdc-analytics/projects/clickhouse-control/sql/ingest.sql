-- Install only after the history/derived state and all replica identities exist.
CREATE TABLE IF NOT EXISTS pipeline.orders_kafka (payload String) ENGINE=Kafka
SETTINGS kafka_broker_list='broker1:9092,broker2:9092,broker3:9092',
 kafka_topic_list='cdc.public.orders',kafka_group_name='orders-control-orders',
 kafka_format='JSONAsString',kafka_num_consumers=1,kafka_max_block_size=10000,
 kafka_flush_interval_ms=1000,kafka_skip_broken_messages=0;
CREATE TABLE IF NOT EXISTS pipeline.customers_kafka (payload String) ENGINE=Kafka
SETTINGS kafka_broker_list='broker1:9092,broker2:9092,broker3:9092',
 kafka_topic_list='cdc.public.customers',kafka_group_name='orders-control-customers',
 kafka_format='JSONAsString',kafka_num_consumers=1,kafka_max_block_size=10000,
 kafka_flush_interval_ms=1000,kafka_skip_broken_messages=0;
CREATE MATERIALIZED VIEW IF NOT EXISTS pipeline.orders_capture TO pipeline.cdc_history AS
SELECT payload,_topic AS topic,toUInt32(_partition) AS partition,_offset AS offset FROM pipeline.orders_kafka;
CREATE MATERIALIZED VIEW IF NOT EXISTS pipeline.customers_capture TO pipeline.cdc_history AS
SELECT payload,_topic AS topic,toUInt32(_partition) AS partition,_offset AS offset FROM pipeline.customers_kafka;
