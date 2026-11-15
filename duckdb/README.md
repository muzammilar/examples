# DuckDB

Website: https://duckdb.org/

- [`single-node/`](single-node) — the DuckDB CLI in Docker over ~1M rows of seeded random CSV/NDJSON: auto-detected reads, joins, QUALIFY/window functions, UNNEST, PIVOT and hive-partitioned Parquet.

- [`zfs-recovery/`](zfs-recovery/README.md) — multi-client Quack server on mirrored ZFS, live disk replacement, retained-disk VM crash, snapshot replication, read-only clones and measured failover loss.

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
