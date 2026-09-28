# TiDB

Website: https://www.pingcap.com/tidb/

- [`docker-compose-cluster/`](docker-compose-cluster) — 3 PD, 3 TiKV and 1 TiDB on Docker Compose; SQL tests for AUTO_RANDOM, region splitting across stores, EXPLAIN ANALYZE coprocessor tasks and optimistic/pessimistic transactions.

## Benchmark

TPC-C (go-tpc) and sysbench on 3 PD + 3 TiKV + 1 TiDB, 6 CPUs / 16 GB (memory raised from the usual 12 GB, because TiKV was OOM-killed at 2.8 GB per store; Apple M4 Pro, Docker VM aarch64, 2026-09-28): TPC-C 4 warehouses 10,092 tpmC (new-order p99 71 ms), point selects 17.4k/s at p99 0.9 ms, `oltp_read_write` 241 tps. Reads go straight to region leaders, and every write transaction pays a two-phase commit over Raft. Full tables and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).
