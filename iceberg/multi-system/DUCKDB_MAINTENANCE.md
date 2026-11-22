# Native DuckDB Iceberg compaction

DuckDB **2.0.1** and Iceberg extension **f979b5b28a** pass the healthy v2
data-file rewrite baseline on a fresh, owned **2,000,000-row** table. The native
C++ CLI performs the workload; the Python standard-library controller reuses
this example's ownership, fencing and resource helpers. It requires no host
database installation or additional database driver.

Run from this directory after `make docker-up` and `make up`, before teardown.
If the native serving tier is running, stop it with `make cluster-down` first;
keep storage and the query coordinator available.

```sh
make duckdb-maintenance ROWS=2000000 CUSTOMERS=10000 BATCH_ROWS=100000
```

The target requires the current profile and creates a UUID-named table. It writes
ignored evidence under `results/current/duckdb-maintenance/<UUID>/`, including
the exact SQL, preserved source, native binary/extension hashes, metadata,
query profiles and resource samples. Retain the ordinary five-command lifecycle
in the main README. This is an additional correctness case; it does not replace
the unfinished live-fault qualification.

## Measured result, 2026-11-22

The final Make workflow passed **215 host controls** and completed in **21.779 s**.
This includes host controls, CLI startup, twenty inserts, body/metadata checks
and process fencing. Infrastructure was already running; image builds are excluded.

| Check | Observed result |
|---|---|
| Input | 2,000,000 unique IDs; 10,000 customers; 20 × 100,000-row inserts |
| Transaction-local rewrite, then rollback | Every body valid inside the transaction; original published snapshot retained after rollback |
| Committed rewrite | 20 live files → 1; both engines verify every deterministic body |
| Retained snapshot | Original 2,000,000 rows still readable after replacement |
| Repeated maintenance | Zero rewritten/added/failed files; snapshot unchanged |
| Wrong-body control | One payload modified inside a transaction; counts and amount sum stay correct, but the body oracle detects it; rollback restores valid data |
| Independent catalog check | Direct local REST read confirms the exact replacement snapshot and one live data file |

The four columns are `event_id BIGINT`, `customer_id INTEGER`,
`amount_cents BIGINT` and `payload_sha256 VARCHAR`. IDs are contiguous; customer
and amount use deterministic modulo arithmetic; payloads contain 64-character
SHA256 hex strings. There are no generated nulls or external input files.
This synthetic fixture has uniform customer access and is not a general corpus.
The oracle checks every field, distinct IDs, bounds and totals. ClickHouse uses
its own SHA256 implementation for independent payload verification.

| Native CALL | Profile time |
|---|---:|
| Rewrite inside the rolled-back transaction | 2,376.375 ms |
| Committed rewrite | 2,149.995 ms |
| No-op rewrite | 17.403 ms |

These are individual DuckDB 2.0 `query.total_time` observations. They share one
CLI process and differ in transaction/cache state; they are not repeated trials,
tail percentiles, or latency-to-durable-commit measurements. The separate
ClickHouse body read took **862.858 ms**, including Docker exec, connection,
query, transfer and JSON decoding. These timings do not rank DuckDB against Spark.

Live file bytes changed **148,637,482 → 146,646,713**. Encoded Parquet column
chunks changed **147,964,402 → 146,579,555 compressed bytes**, with
**161,425,680 → 159,256,426 uncompressed encoded bytes**: **1.0910× → 1.0865×**.
Encoded page bytes are not logical SQL/CSV bytes. The **128 MiB** target produced
a **139.853 MiB** file; it is an approximate target, not a hard file-size cap.
Compression settings/codecs have not been compared.

| Role | Observed CPU, core-s | Mean CPU, one core = 100% | Sampled charged-memory max, MiB |
|---|---:|---:|---:|
| DuckDB CLI | 5.151 | 38.13% | 1,083.3 |
| SeaweedFS | 9.979 | 62.68% | 1,023.9 |
| ClickHouse | 1.924 | 12.09% | 1,299.2 |

The owned active budget is **5 CPUs / 4.5 GiB**: CLI 2 CPUs/2 GiB, storage
1 CPU/1 GiB, and query coordinator 2 CPUs/1.5 GiB. Actual identities and limits
are saved. Host: Apple M4 Pro, 14 logical CPUs/48 GiB; Docker VM: 11 CPUs/about
24.4 GiB, Linux ARM64. Historical storage and an unrelated kind control plane
remained running, so these are functional diagnostics with stated co-tenancy.

Resource windows differ by role and omit unobserved edges. Charged memory
includes file cache/kernel; lifetime backend peaks can precede this trial.
The report retains disk allocation, device read/write bytes and I/O counts for
the two servers. CLI block counters are unavailable, not zero; native query
profiles separately record logical engine I/O. Whole-store allocation includes
other retained fixtures and history, not only this table. See
[`DUCKDB_MAINTENANCE_RESULTS.json`](DUCKDB_MAINTENANCE_RESULTS.json) for counters,
units, source hashes and raw evidence references.

## Failures and remaining work

Two harness attempts remain failed: a case-sensitive Parquet format check, then
a ClickHouse aggregate alias colliding with its input column. Both original
reports, tables and exact source hashes remain preserved. The final fresh run
fixes those checks and uses the current extension-directory/URI settings and
grouped profiling schema. Per `agent-query-safety`, the independent full-body
scan has explicit time, memory, scan and one-row output limits. It deliberately
checks the complete fresh table. Failure-injection controls also verify that
fencing errors cannot skip sampler shutdown and that secondary cleanup/survivor
errors remain recorded.

The pinned [planner](https://github.com/duckdb/duckdb-iceberg/blob/f979b5b28a044c49c38a26636ac01bc1f7047fa5/src/maintenance/rewrite_data_files_planner.cpp#L562-L564)
rejects v3 rewrites. Its [executor](https://github.com/duckdb/duckdb-iceberg/blob/f979b5b28a044c49c38a26636ac01bc1f7047fa5/src/maintenance/rewrite_data_files_executor.cpp)
checks the planned snapshot and rejects pending same-table data/schema changes.
Those are source observations; their exact rejection paths and concurrent-append behavior
still need native tests. API details: [DuckDB 2.0 Iceberg functions](https://duckdb.org/docs/2.0/core_extensions/iceberg/iceberg_functions).

Next, pin/dump timeout and retry behavior, test those rejection/conflict paths,
and keep independent writers/readers live through process and S3/catalog
interruptions. Do not pause writers to force a successful recovery. Committed
deletes, partition evolution, manifest rewrites, expiration/orphan cleanup and
repeated comparative benchmarks remain unrun here. Local DuckDB WAL/file-format
recovery is separate work. The published live compactor-crash pass uses Spark;
this healthy native baseline does not qualify native live-fault recovery.
