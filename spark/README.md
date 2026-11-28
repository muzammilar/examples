# Apache Spark

Distributed SQL, batch processing and structured streaming.

| Example | Status |
|---|---|
| [Local JVM](single-node/) | Million-order join, fresh Spark/Arrow readbacks and active read-process SIGKILL passed |
| [Standalone cluster](docker-compose-cluster/) | Million-order join, active-task worker crash and independent Parquet readbacks passed |
| [HDFS → Spark → Doris](../pipelines/hdfs-spark-doris/) | Million-row batch publications, worker recovery and delayed native HDFS repair; failed 180-second deadline preserved |
| [Kubernetes operator + RF3 HDFS](kubernetes-operator/) | Active executor loss, fresh exact reads and empty DataNode-disk replacement passed; single NameNode |

The separate [Hadoop metadata-HA lab](../hadoop/metadata-ha/) has executed
NameNode fencing/failover and replica checks. Spark on YARN remains pending.
The current batch plan also covers Doris, Flink and storage-backed pipelines.
[Batch plan](../docs/example-plans/flink-doris-spark-storage.md)

[Upstream](https://spark.apache.org/) · [Source](https://github.com/apache/spark)

## Runtime compatibility follow-up

The pinned ARM64 Spark 4.3.0 Ubuntu distribution image reports Python 3.10.12,
below the [documented Python 3.11+ minimum](https://spark.apache.org/docs/4.3.0/).
Existing executed outcomes remain recorded; they do not establish upstream
support for that Python runtime. Before extending those examples, move them to a
supported runtime and requalify the lifecycle. The new
[HDFS/Spark/Doris pipeline](../pipelines/hdfs-spark-doris/) copies the same Spark
distribution into pinned Temurin 17/Noble with Python 3.12 and preserves the
[rejected runtime finding](../pipelines/hdfs-spark-doris/docs/SETUP-FINDINGS.md).
This is a future dependency-maintenance item, not a new level-4 test matrix.
