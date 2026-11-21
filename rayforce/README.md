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

## Limits and remaining experiments

This example embeds the engine and uses its native worker pool, not a distributed
cluster. The Rayforce source pin is a development commit after v3.0.2; the
benchmark does not qualify another release. Full production testing, persisted
storage and crash recovery, graph/as-of workloads, and comparisons using official
language-specific DataFrame interfaces remain unrun. Consult the pinned
[upstream architecture](https://github.com/RayforceDB/rayforce/tree/38fd4be3560d63a5514c1e6883685fe496d45e1a/docs/docs/architecture)
before inferring storage or replication guarantees from in-memory query results.
