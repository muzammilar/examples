# DuckDB

Website: https://duckdb.org/

- [`single-node/`](single-node) — the DuckDB CLI in Docker over ~1M rows of seeded random CSV/NDJSON: auto-detected reads, joins, QUALIFY/window functions, UNNEST, PIVOT and hive-partitioned Parquet.

- [`zfs-recovery/`](zfs-recovery/README.md) — multi-client Quack server on mirrored ZFS, live disk replacement, retained-disk VM crash, snapshot replication, read-only clones and measured failover loss.

## Current-version Iceberg evaluation

[`Iceberg multi-system`](../iceberg/multi-system/projects/duckdb-iceberg/docs/DUCKDB_VERSIONS.md) adds actual
DuckDB **2.0.1** alongside the historical **1.5.5** lane. On the same 2.79-million-row
snapshot, a manifest connection failure aborts the older native process; 2.0.1
returns an ordinary SQL error. Complete dual-engine readback checks 3.39 million
acknowledged rows without missing or wrong bodies. The original combined-fault
recovery case remains failed. These are correctness/error-handling diagnostics;
the older benchmark and ZFS/Quack findings below do not establish 2.0.1 performance
or recovery behavior.

The fresh current Iceberg fixture also passes storage-process recovery with
**2.4 million acknowledged rows** and Spark compactor-crash recovery with
**3.91 million**, checked body-for-body through both engines. Direct DuckDB,
archive handoff and three-shard/three-copy serving paths pass. ClickHouse-written
v2 manifests still omit required metadata, so the complete cross-engine matrix
fails. The earlier memory-failed recovery remains failed; see
[`current results and limitations`](../iceberg/multi-system/docs/ARCHITECTURES.md#current-version-results-2026-11-22).

The [native Iceberg compaction baseline](../iceberg/multi-system/projects/duckdb-iceberg/docs/DUCKDB_MAINTENANCE.md)
also passes **2,000,000-row** rollback/rewrite/retained-snapshot checks,
**20 → 1 files**, and independent body verification, with **215 controls**.
Its **2,149.995 ms** rewrite profile is a functional observation; native live
concurrency and fault recovery remain unfinished.

## Benchmark

TPC-H SF1 on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-09-28): all 22 queries in 0.43 s
(geometric mean 15 ms, slowest 55 ms); the same queries on Parquet files run ~3.3× slower than on
DuckDB's native storage. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).

## Recovery lab

[ZFS recovery](zfs-recovery/README.md) uses 16 GiB mirrored virtual disks and
passed three separate tests: 2,000,000 rows through a member fault/replacement,
1,080,000 acknowledged rows recovered after a retained-disk VM crash
with zero loss, and asynchronous promotion preserving all 1,500,000 replicated
rows. The deliberately unreplicated tail left 30,000 acknowledged rows
absent on the standby. Post-promotion writes lost zero new acknowledgements.
Measurements use software-emulated Linux guests; they are separate from the
native analytical benchmark above. Full timings, warm-query performance and
failure boundaries are in the example README.

## RayforceDB comparison

[`rayforce/duckdb-comparison/`](../rayforce/duckdb-comparison/README.md) compares
both embedded engines through Python, C++ and Rust over 2 million numeric trades
and 10,000 customers, at equal 1/4-CPU and 4-GiB container limits. It includes
exact output checks, repeated warm-query measurements, first-query observations,
CPU time and peak RSS/cgroup memory. DuckDB wins the scans and join in this
workload; Rayforce wins the two group aggregations and bulk CSV loading. Full
numbers and the unrun cold-cache scenarios are in that README. Its separate
[native live WAL profile](../rayforce/duckdb-comparison/projects/duckdb-recovery/docs/DUCKDB_RECOVERY.md)
tests the same two-million-trade fixture with C++ Quack sessions, pinned TLS,
independent readers/writers, two retained-volume SIGKILLs and checkpointing under
load. It is a single-owner recovery test; the existing ZFS lab covers different
storage/replication failure boundaries.

## Future work / TODO

Deferred follow-ups are linked below. Each example keeps its concrete checklist
beside the measured results; experiments remain paused.

| Example | Next work |
|---|---|
| [zfs-recovery](zfs-recovery/README.md#future-work--todo) | Isolate Quack connect/query/fetch/close timing and qualify a supervised deadline with explicit ambiguous-write reconciliation. |
