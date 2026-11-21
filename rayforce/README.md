# RayforceDB

Website: https://rayforcedb.com/

GitHub: https://github.com/RayforceDB/rayforce

| Example | What it demonstrates |
|---|---|
| [`duckdb-comparison/`](duckdb-comparison/README.md) | Embedded numeric analytics against DuckDB through Python, C++ and Rust; 2 million trades, 10,000 customers, exact query results, repeated 1/4-CPU runs, CPU/memory time series, native peaks and disk-I/O/storage accounting |

## Measured comparison

2026-11-21 rerun, Apple M4 Pro, native ARM64 Docker VM. In the four-CPU C++ run,
Rayforce's warm median was **1.251 ms vs DuckDB 3.809 ms** for 256-group
aggregation (**3.04×** lower latency), and **3.635 ms vs 8.345 ms** for 10,000
customer groups (**2.30×** lower latency). DuckDB's join was **6.218 ms vs
Rayforce 21.210 ms** (**3.41×** lower latency). Median process peak RSS for the
complete workload was **247.6 MiB vs Rayforce 380.8 MiB** (**1.54×** lower).
See the example for all three languages, one-CPU results, input checks,
source pins, resource measurements and limitations; this is not a universal ranking.
Exported kernel block counters were zero for these cached in-memory runs; this
does not compare physical disk performance. Persisted-storage compression remains
outside this profile.

The [ingestion comparison](duckdb-comparison/README.md#ingestion-comparison) adds
144 fresh-process trials with 100,000 appends each, batches of 1–10,000 and
full verification of all 2,100,000 final rows per trial. In the one-CPU C++
10,000-row typed path, median observed rates were 19,999,500 rows/s for
Rayforce and 7,032,472 for DuckDB’s row-wise Appender. CPU, memory and latency
costs accompany the rates. These in-memory results do not establish equivalent
transaction isolation, durability or sustained capacity; the columnar Appender
is now measured in a separate [columnar comparison](duckdb-comparison/README.md#columnar-appender-comparison):
144 further trials, with C++ large-batch DuckDB rates of **10.87 million rows/s**
(columnar) versus **6.58 million** (row-wise) in the same run. Rayforce uses
the identical typed implementation in both paths; its columnar-labeled median
was **20.36 million rows/s**. See the trial spread and resource costs before
generalizing from these bounded, single-writer measurements.

## Limits and remaining experiments

This example embeds the engine and uses its native worker pool, not a distributed
cluster. The Rayforce source pin is a development commit after v3.0.2; the
benchmark does not qualify another release. Full production testing,
broader failure/restore boundaries, graph/as-of workloads, and comparisons using official
language-specific DataFrame interfaces remain unrun. Consult the pinned
[upstream architecture](https://github.com/RayforceDB/rayforce/tree/38fd4be3560d63a5514c1e6883685fe496d45e1a/docs/docs/architecture)
before inferring storage or replication guarantees from in-memory query results.

The [saved-snapshot profiles](duckdb-comparison/PERSISTENCE.md) now verify
2,000,000 trade bodies and 10,000 customer bodies after a fresh-process reopen
on both engines. Observed native files were 13,031,576 bytes for Rayforce and
14,954,496 bytes for DuckDB, with CPU/memory/compression/I/O evidence. These
immutable snapshot checks do not qualify live journal or crash recovery.
The separate [synchronous journal test](duckdb-comparison/JOURNAL.md) now recovers
2,251 live ACKs and the full seed after two SIGKILLs, with independent writers,
readers and an external witness. No acknowledged body loss was observed;
asynchronous recovery, corruption, online backup and matched DuckDB recovery remain unrun.
An [offline snapshot/tail restore](duckdb-comparison/JOURNAL.md#offline-restore-into-separate-storage)
also rebuilt every body in separate local storage without access to original input
CSVs: 1,614 replayed entries, zero evaluation errors and zero ACK loss. This does
not establish online backup availability or independent-host durability.
