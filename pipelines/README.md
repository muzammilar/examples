# Cross-system pipelines

These projects evaluate complete data paths, with each lab owning its deployment,
workload, state, results and cleanup. Individual system deployment examples remain
under their system directories. All infrastructure and credentials are local.

- [Order CDC analytics](orders-cdc-analytics/): PostgreSQL → Debezium/Kafka →
  Flink stateful customer join → replicated Doris. Million-order CDC join,
  corrections/deletions, live worker recovery and exact three-copy readback passed;
  initial setup/memory failures remain documented.

The [current batch plan](../docs/example-plans/flink-doris-spark-storage.md)
tracks the subsequent Spark/HDFS/Doris, Iceberg and historical-correction paths,
plus a matched ClickHouse control and bounded storage recovery follow-ups.
