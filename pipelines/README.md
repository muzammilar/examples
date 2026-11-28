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

- [Iceberg snapshot recovery](orders-lakehouse/): Kafka → Flink → Iceberg on
  local SeaweedFS → Spark. Three million acknowledged events, blocked-publication
  recovery, active compaction-worker loss and pinned old snapshots passed exact
  readback. Eight small unreferenced-file candidates are documented and exported.

- [Historical replay beside live CDC](orders-cdc-analytics/projects/historical-replay/):
  Spark recomputed an older snapshot while live revisions/cancellations advanced.
  Publisher crash, original-label commit retry and exact three-copy readback passed
  in the corrected controller; the initial visibility-gate failure and recovery
  remain documented. This project explicitly shares the owning CDC lab.

- [Native ClickHouse control](orders-cdc-analytics/projects/clickhouse-control/):
  the same million-order CDC tape passed exact three-copy validation in both
  backends, with ClickHouse quorum-write recovery. Matched serving budgets and
  unstable post-restart query timings are recorded; no overall winner is claimed.

The [current batch plan](../docs/example-plans/flink-doris-spark-storage.md)
tracks completed paths and bounded storage recovery follow-ups.
