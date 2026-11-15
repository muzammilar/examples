# Apache Iceberg examples

Website: [iceberg.apache.org](https://iceberg.apache.org/)
GitHub: [apache/iceberg](https://github.com/apache/iceberg)

- [Performance benchmark: DuckDB + Iceberg vs ClickHouse + Iceberg vs native ClickHouse](benchmarks/README.md)
- [Shared-storage DuckDB/ClickHouse interoperability, archive and sharded serving examples](multi-system/README.md)

- [Trino + MinIO snapshot recovery](trino-minio/README.md): 1,000,000 orders, REST catalog persistence, active Trino worker loss and MinIO node loss.

| Example | Date / setup | Measured result |
|---|---|---|
| [Multi-system matrix](multi-system/README.md#benchmark) | 2026-11-14, Apple M4 Pro, native aarch64 Docker, bounded containers | Five architectures passed; all 9 serving replicas placed correctly. Warm native 2M-row aggregate p50/p99 4.938/12.986 ms; per-engine/path results and limits linked. |
| [Trino snapshot recovery](trino-minio/#measured-results) | 2026-11-14, Apple M4 Pro, shared native aarch64 Docker | 1,000,000-order CTAS 4.713 s; 256-million-input reference 8.105 s, worker-loss query 54.199 s; 3 failed tasks recovered, 0 acknowledged loss |

## Representative workloads

The existing [20-million-row analytical benchmark](benchmarks/) already exercises Iceberg reads and writes through different engines. [Trino order history](trino-minio/#representative-workload-order-history-and-corrected-analytics) now adds schema evolution, a 100,000-row append, corrections and snapshot queries over 1,000,000 original orders. Tenant queries read 66,900 versus 1,100,000 physical positions; the bucketed layout was slower in the measured small run, as documented. The [multi-system example](multi-system/) now adds five tested SeaweedFS-backed architectures, Spark compaction, both archive directions, and a native ClickHouse serving copy with three shards and three replicas per shard.

## Known issues

The local REST fixture and Trino coordinator are single processes. Their presence does not provide catalog or coordinator HA. Four MinIO nodes share one Docker disk; see the example's failure-domain limits.

[Hoglake](https://github.com/PostHog/hoglake) is recorded in the documentation backlog for future investigation; no Hoglake example is implemented here.
