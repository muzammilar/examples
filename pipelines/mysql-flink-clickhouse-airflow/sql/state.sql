CREATE DATABASE IF NOT EXISTS pipeline;
CREATE TABLE IF NOT EXISTS pipeline.history
(payload String CODEC(ZSTD(3)),topic LowCardinality(String),partition UInt32,offset UInt64)
ENGINE=ReplicatedMergeTree('/mysql-margins/history','{replica}')
ORDER BY (topic,partition,offset)
SETTINGS fsync_after_insert=1,fsync_part_directory=1;
CREATE TABLE IF NOT EXISTS pipeline.margins_state
(
  order_id UInt32,customer_id UInt16,amount_cents Int64,cost_cents Int64,
  shipping_cents Int64,region LowCardinality(String),discount_bps UInt16,
  margin_cents Int64,order_revision UInt32,customer_revision UInt32,event_second UInt32,
  deleted UInt8,partition UInt32,offset UInt64
)
ENGINE=ReplicatedReplacingMergeTree('/mysql-margins/margins','{replica}',offset)
ORDER BY order_id SETTINGS fsync_after_insert=1,fsync_part_directory=1;
CREATE MATERIALIZED VIEW IF NOT EXISTS pipeline.decode_margins TO pipeline.margins_state AS
WITH JSONExtractString(payload,'op') AS op,
  if(op='d',JSONExtractRaw(payload,'before'),JSONExtractRaw(payload,'after')) AS body
SELECT toUInt32(JSONExtractUInt(body,'order_id')) AS order_id,
  toUInt16(JSONExtractUInt(body,'customer_id')) AS customer_id,
  JSONExtractInt(body,'amount_cents') AS amount_cents,
  JSONExtractInt(body,'cost_cents') AS cost_cents,
  JSONExtractInt(body,'shipping_cents') AS shipping_cents,
  JSONExtractString(body,'region') AS region,
  toUInt16(JSONExtractUInt(body,'discount_bps')) AS discount_bps,
  JSONExtractInt(body,'margin_cents') AS margin_cents,
  toUInt32(JSONExtractUInt(body,'order_revision')) AS order_revision,
  toUInt32(JSONExtractUInt(body,'customer_revision')) AS customer_revision,
  toUInt32(JSONExtractUInt(body,'event_second')) AS event_second,
  toUInt8(op='d') AS deleted,partition,offset
FROM pipeline.history WHERE topic='margins.enriched' AND op IN ('c','u','d');
CREATE VIEW IF NOT EXISTS pipeline.current_margins AS
SELECT toInt64(order_id) AS order_id,toInt64(customer_id) AS customer_id,
  amount_cents,cost_cents,shipping_cents,region,toInt64(discount_bps) AS discount_bps,
  margin_cents,toInt64(order_revision) AS order_revision,
  toInt64(customer_revision) AS customer_revision,toInt64(event_second) AS event_second
FROM pipeline.margins_state FINAL WHERE deleted=0;
