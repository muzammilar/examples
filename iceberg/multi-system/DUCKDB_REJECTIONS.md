# Native maintenance rejection checks

`make duckdb-rejections` passes on native **DuckDB 2.0.1**, Iceberg extension
**f979b5b28a**, local SeaweedFS 4.54 and ClickHouse 26.10.10.3. It uses the
example's existing current-profile infrastructure; follow the README's five
lifecycle commands first. The Make workflow passes **217 host controls**.

| Case | Native result | Committed-state verification |
|---|---|---|
| Insert inside an uncommitted transaction, then rewrite | CLI exit 1: `has uncommitted changes in this transaction; commit them first` | Original bodies and metadata unchanged; pending row absent |
| Add a column inside an uncommitted transaction, then rewrite | Same explicit rejection | Original four-column schema and metadata unchanged |
| Rewrite a format-version 3 table | CLI exit 1: `iceberg_rewrite_data_files does not yet support V3 tables` | Native v3 metadata unchanged; table remains empty |

Each rejected CLI closes its own transaction without committing. A fresh native
process verifies the catalog pointer and complete metadata, every body and schema;
ClickHouse independently verifies all ten rows. The v3 case checks an empty v3
table and does not establish support for reading populated v3 tables. Negative
host controls reject an unrelated connection error or changed committed metadata.
These are deliberately **ten-row functional controls**, not a performance or
live-fault test. They establish actual rejection behavior previously observed
only in the pinned extension source.

The reference HTTP policy is explicitly set and dumped: timeout **30 seconds**,
**3 retries**, initial wait **100 ms**, exponential backoff factor **4**. Keep-alive
is true. These settings do **not** establish a hard whole-query deadline or make
retrying arbitrary mutations safe. Memory and temporary spill are each capped at
**1 GB decimal**, with two DuckDB threads; the runner has 2 CPU / 2 GiB limits.
The network-none discovery probe independently observed the same HTTP defaults.

The final run, `b71ed187-c81b-4889-ae1f-91095d93ea3e`, measured:

| Role | Observed CPU, core-s | Mean CPU, one core = 100% | Cgroup lifetime peak, MiB |
|---|---:|---:|---:|
| Native CLI sequence | 1.322 | 31.10% | 48.2 |
| Storage/catalog | 0.180 | 3.20% | 1,029.9 |
| ClickHouse | 1.206 | 21.38% | 1,539.4 |

The runner waits after its short CLI children exit so final cgroup samples include
their CPU and peak memory. Sampled instantaneous memory can miss those children;
the storage/ClickHouse lifetime peaks can predate this run. Role observation
windows differ and omit unobserved edges; CPU includes observation probes. All
roles share one Docker VM, with other retained fixtures present.

The single ten-row Parquet file is **1,702 bytes**, including a **761-byte footer**;
its four encoded chunks are **976 uncompressed / 929 compressed bytes (1.0506×)**.
This tiny fixture is dominated by overhead and provides no useful compression
ranking. Per-role allocated disk, device read/write bytes, operations and rates
are retained in [`DUCKDB_REJECTION_RESULTS.json`](DUCKDB_REJECTION_RESULTS.json).
CLI native block counters are unavailable, not zero. Background storage activity
and earlier retained fixtures affect whole-role counters.

Two intermediate telemetry attempts failed because a table function cannot take
a subquery argument. The final SQL uses a supported session variable. Their
original failures, native logs, source copies and tables remain preserved; they
are harness failures, not successful backend-failure tests. Raw results remain
ignored under `results/current/duckdb-rejections/`; the compact report records
hashes and exact tested source. No earlier table or failed verdict was reset.

The separate [controlled concurrent-append test](DUCKDB_CONFLICTS.md) now passes
stale-commit rejection and fresh-transaction replanning on 110,000 rows. Continuous
writers/readers during native compactor interruption,
catalog/S3 failure, deletes, retention and orphan cleanup remain separate tests.
See the pinned [transaction validation](https://github.com/duckdb/duckdb-iceberg/blob/f979b5b28a044c49c38a26636ac01bc1f7047fa5/src/maintenance/rewrite_data_files_executor.cpp)
and [v3 planner rejection](https://github.com/duckdb/duckdb-iceberg/blob/f979b5b28a044c49c38a26636ac01bc1f7047fa5/src/maintenance/rewrite_data_files_planner.cpp#L562-L564).
