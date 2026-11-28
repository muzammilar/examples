CREATE TABLE IF NOT EXISTS pipeline.kafka_input (payload String) ENGINE=Kafka
SETTINGS kafka_broker_list='broker1:9092,broker2:9092,broker3:9092',
  kafka_topic_list='margins.enriched,cdc.notifications,cdc,margins.isolation,cdc.orders.orders,cdc.orders.customers',
  kafka_group_name='mysql-margins-capture',kafka_format='JSONAsString',
  kafka_num_consumers=1,kafka_max_block_size=10000,kafka_flush_interval_ms=1000,
  kafka_skip_broken_messages=0;
CREATE MATERIALIZED VIEW IF NOT EXISTS pipeline.capture TO pipeline.history AS
SELECT payload,_topic AS topic,toUInt32(_partition) AS partition,_offset AS offset
FROM pipeline.kafka_input;
