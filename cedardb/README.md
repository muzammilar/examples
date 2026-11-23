# CedarDB

Website: https://cedardb.com/

CedarDB is the commercial spin-off of [Umbra](https://umbra-db.com/), the research database from TUM, built by the same team and speaking the PostgreSQL wire protocol.

- [`backup-recovery/`](backup-recovery) — Go writers/readers, one million 256-byte orders per backend, synchronous commits, process crash/reopen and online physical backup/restore against PostgreSQL; full witnesses, CPU/memory, native compression, I/O and disk footprints. Its [analytical contention comparison](backup-recovery/projects/analytical-contention/docs/HTAP.md) adds 12 sustained cells at matching 4-CPU/6-GiB budgets, alone and under live appends/reads.
- [`single-node/`](single-node) — one CedarDB Community Edition server on Docker Compose, loaded with 3M generated rows for analytics, EXPLAIN plans, transactions, CSV and vectors.

CedarDB has no cluster or replication mode yet (high availability is an announced
Enterprise Edition feature), so there is no multi-node example.

The [mutable workload extension](backup-recovery/projects/transactional-mutations/docs/MUTABLE.md) adds Go UPDATE and
DELETE/reinsert transactions, independent snapshot readers and complete versioned
body/receipt checks. Four **million-row** healthy/crash cases verify **2,972 ACKed
transactions** with zero ACK loss or wrong/partial rows. The [12-cell sustained
mutation comparison](backup-recovery/projects/transactional-mutations/docs/MUTABLE_BENCHMARK.md) verifies another
**108,440 ACKs**, with zero loss/corruption: CedarDB query throughput is **3.29×**
PostgreSQL alone and **3.03×** under live mutations, while PostgreSQL uses less
charged memory. CPU, memory, compression, storage and native IOPS are recorded.
Explicit planner preparation, saturation and cold-cache comparisons remain unrun.

## Benchmark

pgbench + analytic queries on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-09-28): 3M orders load in 4.9 s, and most analytic queries over them take 1–100 ms (exact percentiles ~1.3 s); select-only 21.7k TPS with 16 clients. TPC-B tops out around 1.45k TPS: at scale 10 the hot branch rows cause repeatable-read serialization retries on 40–60% of transactions once there are 8+ clients. Full tables and method: [`single-node/README.md`](single-node/README.md#benchmark).

## Future work / TODO

Deferred follow-ups are linked below. Each example keeps its concrete checklist
beside the measured results; experiments remain paused.

| Example | Next work |
|---|---|
| [backup-recovery](backup-recovery/README.md#future-work--todo) | Capture explicitly prepared planner statistics and query plans for both backends before further comparisons. |
