# ClickHouse

Website: https://clickhouse.com/

## Native S3 storage

- [Hot/cold MergeTree on Garage](s3-tiered-storage/): three replicas, native TTL movement, cache benchmarks and separate process, metadata-disk and backend-node failure tests.

## Iceberg examples

- [ClickHouse with Iceberg](iceberg/README.md): direct catalog writes, Spark compaction, retained snapshots and verified archive handoff before TTL deletion, with restart and failure/resume tests.
- [Multi-engine architectures](../iceberg/multi-system/docs/ARCHITECTURES.md): DuckDB/ClickHouse interoperability and a replicated serving cache.

## Iceberg measurements

2026-11-14, Apple M4 Pro, Docker VM aarch64. Warm queries, 2 warmups + 20 samples;
resource caps, raw results and limitations are in each linked README.

| Example | Data | Measured query p50 / p99 |
|---|---:|---|
| [Standalone direct Iceberg](iceberg/README.md#benchmark) | 2,000,000 rows | Full aggregate 16.783 / 23.431 ms |
| [Standalone hot/cold union](iceberg/README.md#benchmark) | 1,000,000 hot + 1,000,000 cold | Full aggregate 12.779 / 13.937 ms |
| [Native sharded serving](../iceberg/multi-system/docs/ARCHITECTURES.md#benchmark) | 2,000,000 logical rows; 3 × 3 copies | Full aggregate 4.938 / 12.986 ms |

These small, compressible workloads are deployment observations, not production
capacity or engine rankings. Iceberg writes in the pinned release are beta.
