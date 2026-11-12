# 20-million-row compaction benchmark

| Setting | Value |
|---|---|
| Run ID | `20261109T155304Z-c7422c` |
| Profile | mixed |
| Rows | 20,000,000 initial; ten batches of 1,000 appended rows and 2,000 updated rows |
| Timed reads | 5 per query before and after compaction |
| Limits | 4 CPUs, 6 GiB per container |
Versions: DuckDB v1.5.5, ClickHouse 26.10.2.103

All systems returned identical logical results before and after the compaction phase. The operations are engine-specific: DuckDB data-file rewriting was unavailable in the pinned extension; ClickHouse Iceberg applied position deletes and rewrote manifests; native ClickHouse merged parts and deduplicated versions. Times are client wall time in milliseconds.

| System | Append median/batch | Update median/batch | Compaction operation | Compaction time | Full scan before → after | Group by before → after |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| DuckDB + Iceberg | 15.0 | 89.3 | unavailable in DuckDB v1.5.5 | n/a | 32.5 → 32.1 | 37.6 → 36.6 |
| ClickHouse + Iceberg | 67.0 | 189.5 | apply position deletes | 5,225.2 | 64.1 → 53.7 | 108.6 → 103.5 |
| ClickHouse native | 58.3 | 68.5 | merge parts and deduplicate | 1,450.8 | 157.9 → 39.2 | 172.0 → 44.8 |

ClickHouse Iceberg also ran a separate manifest rewrite in 1.1 ms. It is listed separately in the raw JSON. Its support is for merging position deletes into data files and rewriting manifests; it does not provide general small-data-file compaction. ClickHouse native's forced merge substantially reduced the measured `FINAL` query latency in this workload. DuckDB's extension did not expose a compaction function, so the DuckDB read timings are before/after checks without physical maintenance.

These are one-run maintenance measurements on a 20-million-row dataset. There are ten write samples per operation family; read medians use five repetitions. The write and compaction values should be rerun on the target workload before drawing broad conclusions.

## Raw output

- [Comparison JSON](iceberg-comparison-20261109T155304Z-c7422c.json)
- [DuckDB + Iceberg JSON](iceberg-benchmark-20261109T155304Z-c7422c-duckdb-iceberg.json)
- [ClickHouse + Iceberg JSON](iceberg-benchmark-20261109T155304Z-c7422c-clickhouse-iceberg.json)
- [ClickHouse native JSON](iceberg-benchmark-20261109T155304Z-c7422c-clickhouse-native.json)

## References

- [DuckDB Iceberg maintenance functions](https://duckdb.org/docs/current/core_extensions/iceberg/reference#iceberg_rewrite_data_files)
- [ClickHouse Iceberg support matrix](https://github.com/ClickHouse/clickhouse-docs/blob/main/docs/use-cases/data_lake/support-matrix.md)
- [ClickHouse 26.7 manifest compaction announcement](https://presentations.clickhouse.com/2026-release-26.7/)
- [ClickHouse guidance on `OPTIMIZE TABLE ... FINAL`](https://clickhouse.com/resources/engineering/clickhouse-optimize-table-final)
