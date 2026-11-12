# Next benchmark tests

## Scope and current baseline

Keep this project focused on three isolated paths: DuckDB + Iceberg, ClickHouse + Iceberg, and ClickHouse native `MergeTree`. The Go driver is intentionally part of the timed path and is assumed to be the bottleneck under high write concurrency. Measure driver throughput, CPU use, latency, and backpressure while keeping the generated input and logical results consistent.

Do not add the separate S3-compatible object-storage/shared-table experiment or the ClickHouse data-cache comparison here. For the latter, keep ClickHouse's data cache disabled or at one fixed state; the cache behavior has its own project.

The current run covers 20 million narrow rows, three aggregate query shapes, sequential writes, and one maintenance phase. The 20-million-row compaction run uses ten batches of 1,000 appends and 2,000 updates. It does not yet give us repeated independent trials, sustained writes, multiple client writers, varied query selectivity, or pre/post physical file and part counts. DuckDB 1.5.5 also lacks the documented Iceberg rewrite procedure that the current docs describe.

## Priority 1: make the numbers repeatable and explainable

1. Add independent benchmark repetitions, separate from `RUNS` (which repeats reads inside one run). Start with five independent runs per configuration; report median and p95 for reads, writes, initial load, and each maintenance operation. Preserve each run's raw JSON.
2. Rotate the system execution order across repetitions. The controller currently always runs DuckDB, ClickHouse + Iceberg, then native ClickHouse, which can bias results through host page cache and prior activity.
3. Record the physical work: Iceberg data files, delete files, manifests, snapshots, and bytes before/after writes and maintenance; native ClickHouse active parts, bytes, and merge activity before/after maintenance. DuckDB's Iceberg metadata functions expose file and manifest rows; ClickHouse exposes active parts and merge state through system tables.
4. Record driver CPU, container CPU/memory, input bytes, output rows, and errors/retries. Report rows/s and bytes/s alongside operation latency. Keep generation and warm-up outside timed intervals.
5. Label cache state and run order. Do not claim a cold-cache result unless the host/runtime cache was actually controlled; a fresh container alone does not guarantee that.

## Priority 2: write batch and writer-concurrency matrix

Run the same total row volume with batch sizes of 1,000, 10,000, and 100,000 rows, and with 1, 4, and 16 Go writers. Hold total generated rows constant for the first comparison, then run a fixed per-writer rate to find the driver's saturation point. Record per-writer and aggregate throughput, p50/p95/p99 commit latency, queueing, CPU, and commit failures.

For ClickHouse native, compare synchronous native-protocol inserts with `async_insert=1` and `wait_for_async_insert=1` as a separate ingestion mode. Keep the acknowledgment/durability setting explicit. This tests client-side versus server-side batching without silently mixing the two latency definitions. ClickHouse documents both batching and asynchronous inserts as distinct approaches; the best choice depends on producer shape and latency needs. [ClickHouse asynchronous inserts](https://clickhouse.com/blog/asynchronous-data-inserts-in-clickhouse)

Multi-writer tests in this project should have multiple writers to the same system's isolated table. Cross-engine concurrent writers to one shared Iceberg table belong in the separate interoperability project. Iceberg uses optimistic concurrency, so that project should measure commit conflicts, retries, and successful committed throughput, not just client request rate. [Iceberg reliability and concurrent writes](https://iceberg.apache.org/docs/latest/reliability/)

## Priority 3: update shape and maintenance pressure

Vary updated-key fraction (0.01%, 1%, and 10%), key distribution (uniform and a hot-key/skewed case), and update cadence. Keep the number of changed logical rows explicit. Include a workload where updates repeatedly target the same keys and one where each batch targets new keys. Continue to report ClickHouse Iceberg's delete-plus-insert workaround separately from DuckDB `MERGE` and native `ReplacingMergeTree` versioned inserts.

Create fragmentation with 30 and 100 small write batches, then measure before and after maintenance:

- DuckDB: verify whether the pinned release supports `iceberg_rewrite_data_files`; if not, keep the capability result visible or upgrade the pin before comparing physical compaction.
- ClickHouse + Iceberg: time position-delete application and manifest rewrite separately. Current support is experimental and does not include general small-data-file compaction. [ClickHouse Iceberg support matrix](https://github.com/ClickHouse/clickhouse-docs/blob/main/docs/use-cases/data_lake/support-matrix.md)
- ClickHouse native: measure natural background merges separately from explicit `OPTIMIZE ... FINAL`. The latter forces a merge and should be treated as administrative maintenance, not the default production policy. [ClickHouse `OPTIMIZE ... FINAL` guidance](https://clickhouse.com/resources/engineering/clickhouse-optimize-table-final)

Compare maintenance time, files/parts and bytes removed/rewritten, write amplification, query latency before/after, and logical correctness. Do not combine these engine-specific operations into one score.

## Priority 4: query mix and data shape

Expand the read suite beyond the current 1% range filter and 32-group aggregation:

- Selectivity: point key, 0.01%, 1%, 10%, and full scan.
- Projection: key only, a few columns, and all columns; vary row width with a configurable payload.
- Aggregation: current 32 groups and a high-cardinality grouping key.
- Mixed read/write: readers query while writes arrive; measure query p95/p99, write p95/p99, and visible-data lag.

Add joins only if the benchmark's intended use includes them; keep query semantics and output checksums identical across engines.

## Priority 5: sustained run and resource sensitivity

Run a 15-minute mixed workload with a fixed arrival schedule and concurrent readers/writers. Report throughput over time, latency percentiles, ClickHouse merge backlog, Iceberg file/manifest growth, storage bytes, and whether performance degrades as metadata accumulates. Use multiple repetitions before declaring a winner.

After that baseline is stable, sweep CPU limits (2, 4, 8) and memory limits, one variable at a time. Keep ClickHouse data-cache experiments out of this sweep so cache policy does not become a hidden variable.

## Execution order

1. Add independent repetitions, order rotation, driver/resource counters, and physical layout metrics.
2. Run the batch-size and writer-count matrix.
3. Add skewed update and 30/100-batch compaction cases.
4. Expand selectivity/projection/cardinality and concurrent readers.
5. Run the sustained mixed workload; then test resource sensitivity.

Every result should record exact engine/client versions, row count and row width, batch size, writer count, query mix, resource limits, run order, cache label, physical layout metrics, correctness status, and raw per-operation samples.

## Research references

- [ClickHouse insert benchmark methodology](https://clickhouse.com/blog/building-clickcannon-a-tool-for-benchmark-clickhouse) discusses sustained throughput, configurable batch sizes, realistic data timing, and merge behavior.
- [ClickHouse asynchronous inserts](https://clickhouse.com/blog/asynchronous-data-inserts-in-clickhouse) compares client batching and server-side buffering.
- [ClickHouse `OPTIMIZE ... FINAL` guidance](https://clickhouse.com/resources/engineering/clickhouse-optimize-table-final) describes background merge behavior and why forced final merges should be bounded.
- [Iceberg reliability](https://iceberg.apache.org/docs/latest/reliability/) describes snapshot isolation, optimistic concurrency, conflict retries, and safe file replacement.
- [DuckDB Iceberg functions](https://duckdb.org/docs/current/core_extensions/iceberg/reference) documents file/manifest inspection and data-file rewrite maintenance functions.
