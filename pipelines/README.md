# Cross-system pipelines

These projects evaluate complete data paths, with each lab owning its deployment,
workload, state, results and cleanup. Individual system deployment examples remain
under their system directories. All infrastructure and credentials are local.

- [Order CDC analytics](orders-cdc-analytics/): PostgreSQL → Debezium/Kafka →
  Flink stateful customer join → replicated Doris. Million-order CDC join,
  corrections/deletions, live worker recovery and exact three-copy readback passed;
  initial setup/memory failures remain documented.

- [HDFS batch analytics](hdfs-spark-doris/): daily Parquet inputs → distributed
  Spark join/aggregation → replicated Doris. Three million-row output batches,
  live worker/DataNode failures and exact physical-copy readbacks ran. Stock HDFS
  repair missed the 180-second gate; all copies recovered automatically later.

The [current batch plan](../docs/example-plans/flink-doris-spark-storage.md)
tracks the subsequent Spark/HDFS/Doris, Iceberg and historical-correction paths,
plus a matched ClickHouse control and bounded storage recovery follow-ups.
