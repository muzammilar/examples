# Apache Flink

Stateful stream processing and distributed batch SQL.

| Example | Status |
|---|---|
| [Local-mode SQL](single-node/) | Million-order independent body verification passed; no HA |
| [Distributed order processing](docker-compose-cluster/) | Million-order baseline and checkpointed worker recovery passed; extended coverage pending |

[Kubernetes operator](kubernetes-operator/) passed the clean million-order run,
retained repeated setup and combined worker/S3-capacity recovery; the storage
stall, initial disk block and remaining scaling/HA gaps stay documented.
Cross-system pipeline examples are pending. Use
Flink SQL for engine operations and Rust, C++ or Go where supported for external
workload applications. Record connector compatibility separately from engine releases.

[Upstream](https://flink.apache.org/) · [Source](https://github.com/apache/flink)
