# Apache Spark

Distributed SQL, batch processing and structured streaming.

| Example | Status |
|---|---|
| [Local JVM](single-node/) | Million-order join, fresh Spark/Arrow readbacks and active read-process SIGKILL passed |
| [Standalone cluster](docker-compose-cluster/) | Million-order join, active-task worker crash and independent Parquet readbacks passed |
| [Kubernetes operator + RF3 HDFS](kubernetes-operator/) | Active executor loss, fresh exact reads and empty DataNode-disk replacement passed; single NameNode |

HDFS metadata HA and YARN execution are pending.
The current batch plan also covers Doris, Flink and storage-backed pipelines.
[Batch plan](../docs/example-plans/flink-doris-spark-storage.md)

[Upstream](https://spark.apache.org/) · [Source](https://github.com/apache/spark)
