# 20-million-row mixed benchmark

| Setting | Value |
|---|---|
| Run ID | `20261109T152055Z-f9ef5b` |
| Profile | mixed |
| Rows | 20,000,000 initial, plus 200,000 appended and 200,000 updated |
| Write batches | 1 |
| Timed reads | 5 per query |
| Limits | 4 CPUs, 6 GiB per container |
Versions: DuckDB v1.5.5, ClickHouse 26.10.2.103

All systems returned identical baseline and final query results. Timings are client wall time through the Go drivers, in milliseconds. Read values are medians across five timed runs. There was one append and one update sample per system, so those write values are individual observations, not stable medians.

| System | Initial load | Full scan | Group by | Selective | Append batch | Update batch | Append rows/s | Updated rows/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DuckDB + Iceberg | 425.7 | 43.2 | 51.2 | 18.4 | 58.1 | 150.0 | 3,443,170 | 1,332,907 |
| ClickHouse + Iceberg | 5,371.6 | 60.8 | 107.8 | 9.6 | 117.5 | 252.3 | 1,701,462 | 792,704 |
| ClickHouse native | 7,425.7 | 145.6 | 146.6 | 4.8 | 138.8 | 125.3 | 1,441,234 | 1,596,462 |

## Interpretation

- DuckDB + Iceberg had the lowest initial-load, full-scan, grouped-query, append, and update latency in this run.
- ClickHouse native had the fastest selective query and fastest update operation.
- ClickHouse + Iceberg fell between the other systems for full scan and selective read, and had the slowest update operation. Its update is a synchronous delete plus insert across Iceberg commits; native ClickHouse uses versioned inserts.
- The read comparison has five timed samples per query. Each write operation has one sample, so repeat with more write batches before treating the write ranking as stable.
- This is a pre-compaction result; the follow-up [20-million-row compaction run](20-million-row-compaction.md) includes engine-specific maintenance and ten small write batches.

## Raw output

- [Comparison JSON](iceberg-comparison-20261109T152055Z-f9ef5b.json)
- [DuckDB + Iceberg JSON](iceberg-benchmark-20261109T152055Z-f9ef5b-duckdb-iceberg.json)
- [ClickHouse + Iceberg JSON](iceberg-benchmark-20261109T152055Z-f9ef5b-clickhouse-iceberg.json)
- [ClickHouse native JSON](iceberg-benchmark-20261109T152055Z-f9ef5b-clickhouse-native.json)
