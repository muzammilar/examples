# Apache Iceberg examples

Website: [iceberg.apache.org](https://iceberg.apache.org/)
GitHub: [apache/iceberg](https://github.com/apache/iceberg)

- [Performance benchmark: DuckDB + Iceberg vs ClickHouse + Iceberg vs native ClickHouse](benchmarks/README.md)
- [Shared-storage multi-writer and ClickHouse cluster example](multi-system/README.md)

- [Trino + MinIO snapshot recovery](trino-minio/README.md): 1,000,000 orders, REST catalog persistence, active Trino worker loss and MinIO node loss.

| Example | Date / setup | Measured result |
|---|---|---|
| [Trino snapshot recovery](trino-minio/#measured-results) | 2026-11-14, Apple M4 Pro, shared native aarch64 Docker | 1,000,000-order CTAS 4.713 s; 256-million-input reference 8.105 s, worker-loss query 54.199 s; 3 failed tasks recovered, 0 acknowledged loss |

## Representative workloads

The existing [20-million-row analytical benchmark](benchmarks/) already exercises Iceberg reads and writes through different engines. [Trino order history](trino-minio/#representative-workload-order-history-and-corrected-analytics) now adds schema evolution, a 100,000-row append, corrections and snapshot queries over 1,000,000 original orders. Tenant queries read 66,900 versus 1,100,000 physical positions; the bucketed layout was slower in the measured small run, as documented. These are runnable workloads; the separate multi-system design remains a plan.

## Known issues

The local REST fixture and Trino coordinator are single processes. Their presence does not provide catalog or coordinator HA. Four MinIO nodes share one Docker disk; see the example's failure-domain limits.

[Hoglake](https://github.com/PostHog/hoglake) is recorded in the documentation backlog for future investigation; no Hoglake example is implemented here.
