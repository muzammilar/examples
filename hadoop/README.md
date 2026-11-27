# Apache Hadoop

HDFS distributed storage and YARN resource scheduling.

| Example | Status |
|---|---|
| [Pseudo-distributed HDFS](single-node/) | 64 MiB full-body/hash baseline and retained DataNode crash passed; HA pending |
| [Metadata HA](metadata-ha/) | Three-JournalNode quorum, RF3 physical hashes and live bounded-OpenSSH failover passed; native JSch stall retained |
| [Spark operator on RF3 HDFS](../spark/kubernetes-operator/) | Three physical copies verified; empty disk replacement and exact retained-data reads passed |

HDFS Kubernetes metadata HA and Spark on YARN remain pending. The metadata HA
Compose lab is separate from the RF1 baseline and Spark-owned Kubernetes HDFS.
The Spark example owns its Kubernetes storage; it does not require this single-node lab.
[Current batch](../docs/example-plans/flink-doris-spark-storage.md)

[Upstream](https://hadoop.apache.org/) · [Source](https://github.com/apache/hadoop)
