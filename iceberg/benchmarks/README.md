# DuckDB and ClickHouse Iceberg benchmarks

This is the standalone performance benchmark project. Compare these three paths on identical logical data and workload:

1. DuckDB querying an Iceberg table.
2. ClickHouse querying an Iceberg table.
3. ClickHouse querying its native `MergeTree` copy.

See [the next-test plan](PLAN.md) for the workload matrix and validation steps beyond the current baseline.

The benchmark uses one root Compose file that includes a separate Compose file for each system. The Makefile runs the systems serially against the same deterministic input and shared local-filesystem Iceberg warehouse. The controller, workload generator, and all benchmark drivers are written in Go. Timings are client wall time through the Go database/native clients, including driver encoding and protocol overhead; the input generator runs outside the timed operations. This intentionally makes the client path part of the comparison. It does not include the separate SeaweedFS/S3-compatible multi-system example, the 2-shard/2-replica ClickHouse cluster, or N-writer concurrency experiment.

## Topology

```text
make benchmarks
  ├── duckdb-iceberg Compose: DuckDB + REST fixture ──┐
  ├── clickhouse-iceberg Compose: ClickHouse ─────────┼─ shared local warehouse
  └── clickhouse-native Compose: ClickHouse ──────────┘
```

Each benchmark starts only its own containers, measures the system, then tears those containers down before the next system starts. The persistent warehouse and input volumes remain between runs. Each system loads the same generated seed into its own table; the native ClickHouse table uses its own local data volume. The Apache Iceberg REST fixture is a lightweight local test catalog, not a production catalog.

## Code layout

- `duckdb-iceberg/`: DuckDB Go client for Iceberg setup, reads, append, and `MERGE`.
- `clickhouse-iceberg/`: `IcebergLocal` setup, reads, append, and delete-plus-insert update operations.
- `clickhouse-native/`: native `ReplacingMergeTree` setup, reads, append, and versioned-row updates.
- `clickhouse-client/`: high-throughput ClickHouse Go client over the native protocol; it streams Parquet rows with the client's batch API.
- `workload-generator/`: generates the shared deterministic Parquet seed and optional append/update batches using DuckDB's Go client.
- `cmd/benchmark/`: timed workload runner, built into each system's benchmark container.
- `cmd/benchctl/`: host-side Go controller that builds, runs, and tears down each subsystem serially, then validates and summarizes results.

From `iceberg/benchmarks/`, run `make benchmarks` for the default 20-million-row mixed workload, or override `ROWS`, `PROFILE`, `RUNS`, and `BATCHES`. Use `BATCHES=100` to exercise many writes per run. Run one subsystem manually with `make up SYSTEM=clickhouse-iceberg`; stop subsystems with `make down`. `make benchmarks` runs all three systems serially, checks logical results before and after maintenance, prints a comparison, and writes raw per-system JSON plus a comparison JSON under `results/`.

## Workloads

Do not collapse this into one score. Run each workload profile independently and allow its parameters to vary:

- **Read-heavy:** repeated selective aggregates and full scans; vary table size, selectivity, and query repetitions.
- **Append-heavy:** append batches; vary batch size and rows per second.
- **Update-heavy:** change existing keys; vary update fraction, batch size, and update frequency.
- **Mixed:** apply append and update batches in one run, with pre-write and post-write reads. The current first version runs these phases sequentially; concurrent reader/writer mixes are a follow-up workload.

The generator must be deterministic. Each profile starts from the same logical seed data. Use the same rows, key distribution, and SQL result semantics for all comparable measurements. Record the parameters with every result so workload shapes can be compared without treating one mix as universal.

## Fairness and interpretation

- Run one benchmark engine at a time. Give DuckDB and ClickHouse the same CPU and memory limits. The Go runner records end-to-end operation latency; ClickHouse inserts use its native protocol and batch API. The REST fixture stays idle during table scans; record the storage as a local Docker volume.
- Separate initial load, warm-up, and timed work. Report warm and cold reads separately where cache state can be controlled. Use several rounds and report medians plus per-operation results.
- Record engine versions, resource limits, dataset bytes/rows, Iceberg data-file count and sizes, and the exact profile parameters. Do not compare only aggregate elapsed time.
- For read comparisons, verify returned row counts/checksums first. `ClickHouse + Iceberg` and native `MergeTree` have different storage layouts, so include both scan-heavy and selective queries.
- For writes, report operation latency/throughput and resulting Iceberg file/manifest or ClickHouse part counts. A small-batch workload can favor one system while creating too many files or parts; that is part of the result.
- The native ClickHouse case is a `MergeTree` table on ClickHouse local storage, not a copy of the Iceberg query path. Its initial load and ongoing freshness costs must be reported separately from query latency.

## Update semantics are a capability result

DuckDB's Iceberg extension documents `INSERT`, `UPDATE`, `DELETE`, and `MERGE` with an attached REST catalog. Current ClickHouse documentation lists Iceberg `INSERT` as Beta and `DELETE` as experimental, but does not support `UPDATE` or `MERGE`. Therefore:

- Benchmark DuckDB Iceberg updates using the documented Iceberg operation.
- Benchmark ClickHouse native updates with a suitable versioned-row design such as `ReplacingMergeTree`, and measure the query cost needed to return the latest version correctly.
- For ClickHouse Iceberg, benchmark supported inserts and reads. An optional delete-then-insert update workaround must be reported separately because it is two commits and is not an atomic `MERGE`.
- Concurrent readers and writers are not part of this first benchmark pass. The separate multi-system example covers shared-table interoperability and concurrent writers/readers.

This is an important result, not a benchmark failure: a system that cannot perform the required operation may lose the workload before performance is considered.

## Initial query and write measurements

Keep the first suite small and extensible:

1. `COUNT`, `SUM`, and grouped aggregation over the full table.
2. The same aggregation with a selective key/range filter.
3. Append one batch, then verify the row count and aggregate checksum.
4. Upsert a configurable fraction of existing keys, then verify latest-version results.
5. Add concurrent readers/writers as a separate workload after the sequential baselines are stable.

The first implementation covers these operations with configurable row count, batch rows, update fraction, query repetitions, CPU, and memory. It saves machine metadata and raw timings as JSON under `results/`.

### Initial smoke run

The first end-to-end run used 20,000 initial rows, two batches, 1,000 appended rows and 200 updated rows per batch, one timed read per query, and 4 CPU / 6 GiB limits. All systems returned identical baseline and final query results. Client wall times in milliseconds:

| System | Initial load | Full scan | Group by | Selective | Append batch | Update batch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DuckDB + Iceberg | 126.1 | 6.7 | 8.3 | 7.4 | 18.5 | 19.5 |
| ClickHouse + Iceberg | 73.0 | 12.2 | 3.6 | 3.6 | 56.8 | 65.4 |
| ClickHouse native | 73.5 | 9.8 | 2.4 | 2.0 | 55.5 | 55.6 |

This is a wiring/smoke result, not a performance conclusion: one timed read per query and two write samples are too few for stable percentiles, and 20,000 rows is small. The ClickHouse Iceberg update is synchronous delete plus insert, while native ClickHouse uses versioned inserts; those timings reflect the implemented operation semantics. Use multiple runs and larger datasets before drawing conclusions. The machine-local raw data is `results/iceberg-comparison-20261109T151325Z-f3b3b0.json` plus the three same-run per-system JSON files.

### 20-million-row mixed run

The first larger run used the 20-million-row default but did not include maintenance, so treat it as a pre-compaction baseline. Its result is in [the 20-million-row baseline note](results/20-million-row-mixed.md). The follow-up [20-million-row compaction run](results/20-million-row-compaction.md) measures maintenance after ten small append/update batches.

## Compaction is engine-specific

The runner records compaction separately from insert/update latency and measures reads again afterward. DuckDB's current docs describe `iceberg_rewrite_data_files` as rewriting Iceberg data files and applying pending deletes, but the function is unavailable in the benchmark's pinned DuckDB v1.5.5 extension, so this run records that capability gap rather than substituting a different engine. ClickHouse Iceberg's experimental `OPTIMIZE TABLE` applies position deletes into data files; `OPTIMIZE TABLE ... MANIFEST` rewrites metadata. The ClickHouse support matrix says this is not general small-data-file compaction. The native `MergeTree` path uses `OPTIMIZE ... FINAL` to merge parts and deduplicate versions; it is a forced maintenance operation, not the normal background-merge policy. Therefore compaction latency is reported with its operation name and should not be treated as identical work across all engines.

## Implementation status

The first three benchmark paths, per-system Compose files, root Compose includes, Go controller and drivers, deterministic shared workload, correctness comparison, and JSON result output are implemented. Cross-engine interoperability and concurrent workloads remain follow-up experiments.

ClickHouse's experimental Iceberg data cache is outside this project's scope. It belongs in a separate cache benchmark so it does not become another variable in this three-path comparison.

## Known ClickHouse guidance applied

- `decision-ingestion-strategy`: vary batching and producer shape; do not assume one insert strategy fits every profile.
- `decision-late-arriving-upserts` and `insert-mutation-avoid-update`: use versioned rows/`ReplacingMergeTree` for frequent state replacement instead of treating expensive mutations as the default.
- `insert-batch-size`: include realistic batch sizes and small-batch stress cases; the stress case should be identified as such.

These are design hypotheses for the benchmark. The measurements should show where they hold for this dataset and resource budget.

## Sources checked 2026-11-09

- [DuckDB Iceberg writes](https://duckdb.org/docs/current/core_extensions/iceberg/writing)
- [DuckDB SeaweedFS catalog setup](https://duckdb.org/docs/current/core_extensions/iceberg/catalogs)
- [ClickHouse Iceberg support matrix](https://github.com/ClickHouse/clickhouse-docs/blob/main/docs/use-cases/data_lake/support-matrix.md)
- [ClickHouse with SeaweedFS table buckets](https://seaweedfs.com/blog/clickhouse-table-buckets/)
- [Apache Iceberg concurrent-write reliability](https://iceberg.apache.org/docs/latest/reliability/)
