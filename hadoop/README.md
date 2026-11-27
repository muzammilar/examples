# Apache Hadoop

HDFS distributed storage and YARN resource scheduling.

| Example | Status |
|---|---|
| [Pseudo-distributed HDFS](single-node/) | 64 MiB full-body/hash baseline and retained DataNode crash passed; HA pending |
| [Spark operator on RF3 HDFS](../spark/kubernetes-operator/) | Three physical copies verified; empty disk replacement and exact retained-data reads passed |

Replicated/HA Compose, HDFS metadata HA and Spark on YARN are pending.
The Spark example owns its Kubernetes storage; it does not require this single-node lab.
[Current batch](../docs/example-plans/flink-doris-spark-storage.md)

[Upstream](https://hadoop.apache.org/) · [Source](https://github.com/apache/hadoop)
