# Apache Doris

Mutable analytical serving with columnar storage, distributed SQL and native joins.

| Example | Status |
|---|---|
| [Single-node](single-node/) | Million-row full-body baseline and retained BE crash passed; RF1 only |
| [Replicated mutable orders](docker-compose-cluster/) | RF3 / three FE voters; million-row upserts and three fixed-replica reads passed. Immediate outage query failed; fresh reads recovered in 42.57 s. |

Private kind/operator and CDC/pipeline examples are pending.
[Current batch](../docs/example-plans/flink-doris-spark-storage.md)

[Upstream](https://doris.apache.org/) · [Source](https://github.com/apache/doris)
