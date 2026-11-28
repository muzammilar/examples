CREATE DATABASE IF NOT EXISTS pipeline;
CREATE TABLE IF NOT EXISTS pipeline.cdc_history
(payload String CODEC(ZSTD(3)), topic LowCardinality(String) CODEC(ZSTD(3)), partition UInt32 CODEC(ZSTD(3)), offset UInt64 CODEC(ZSTD(3)))
ENGINE=ReplicatedMergeTree('/orders-control/history','{replica}')
ORDER BY (topic,partition,offset)
SETTINGS fsync_after_insert=1,fsync_part_directory=1;
CREATE TABLE IF NOT EXISTS pipeline.orders_state
(id UInt32 CODEC(ZSTD(3)),customer_id UInt16 CODEC(ZSTD(3)),amount_cents Int64 CODEC(ZSTD(3)),revision UInt32 CODEC(ZSTD(3)),cancelled UInt8 CODEC(ZSTD(3)),
 event_second UInt32 CODEC(ZSTD(3)),deleted UInt8 CODEC(ZSTD(3)),lsn UInt64 CODEC(ZSTD(3)))
ENGINE=ReplicatedReplacingMergeTree('/orders-control/orders','{replica}',lsn)
ORDER BY id SETTINGS fsync_after_insert=1,fsync_part_directory=1;
CREATE TABLE IF NOT EXISTS pipeline.customers_state
(id UInt16 CODEC(ZSTD(3)),region LowCardinality(String) CODEC(ZSTD(3)),revision UInt32 CODEC(ZSTD(3)),deleted UInt8 CODEC(ZSTD(3)),lsn UInt64 CODEC(ZSTD(3)))
ENGINE=ReplicatedReplacingMergeTree('/orders-control/customers','{replica}',lsn)
ORDER BY id SETTINGS fsync_after_insert=1,fsync_part_directory=1;
CREATE MATERIALIZED VIEW IF NOT EXISTS pipeline.orders_decode TO pipeline.orders_state AS
WITH JSONExtractString(payload,'op') AS op,
 if(op='d',JSONExtractRaw(payload,'before'),JSONExtractRaw(payload,'after')) AS body
SELECT toUInt32(JSONExtractUInt(body,'id')) AS id,
 toUInt16(JSONExtractUInt(body,'customer_id')) AS customer_id,
 JSONExtractInt(body,'amount_cents') AS amount_cents,
 toUInt32(JSONExtractUInt(body,'revision')) AS revision,
 toUInt8(JSONExtractBool(body,'cancelled')) AS cancelled,
 toUInt32(JSONExtractUInt(body,'event_second')) AS event_second,
 toUInt8(op='d') AS deleted,JSONExtractUInt(payload,'source','lsn') AS lsn
FROM pipeline.cdc_history WHERE topic='cdc.public.orders';
CREATE MATERIALIZED VIEW IF NOT EXISTS pipeline.customers_decode TO pipeline.customers_state AS
WITH JSONExtractString(payload,'op') AS op,
 if(op='d',JSONExtractRaw(payload,'before'),JSONExtractRaw(payload,'after')) AS body
SELECT toUInt16(JSONExtractUInt(body,'id')) AS id,
 JSONExtractString(body,'region') AS region,
 toUInt32(JSONExtractUInt(body,'revision')) AS revision,
 toUInt8(op='d') AS deleted,JSONExtractUInt(payload,'source','lsn') AS lsn
FROM pipeline.cdc_history WHERE topic='cdc.public.customers';
CREATE VIEW IF NOT EXISTS pipeline.current_comparison AS
SELECT toInt64(o.id) AS order_id,toInt64(o.customer_id) AS customer_id,
 o.amount_cents,c.region,toInt64(o.revision) AS order_revision,
 toInt64(c.revision) AS customer_revision,toInt64(o.event_second) AS event_second,
 toInt64(o.revision)*1000000+toInt64(c.revision) AS version
FROM (SELECT * FROM pipeline.orders_state FINAL WHERE deleted=0 AND cancelled=0) o
INNER JOIN (SELECT * FROM pipeline.customers_state FINAL WHERE deleted=0) c ON o.customer_id=c.id;

CREATE TABLE IF NOT EXISTS pipeline.write_witness
(run String,sequence UInt64 CODEC(ZSTD(3)),payload String CODEC(ZSTD(3)))
ENGINE=ReplicatedMergeTree('/orders-control/witness','{replica}')
ORDER BY (run,sequence) SETTINGS fsync_after_insert=1,fsync_part_directory=1;
