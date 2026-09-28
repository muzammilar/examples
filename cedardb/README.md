# CedarDB

Website: https://cedardb.com/

CedarDB is the commercial spin-off of [Umbra](https://umbra-db.com/), the research database from TUM, built by the same team and speaking the PostgreSQL wire protocol.

- [`single-node/`](single-node) — one CedarDB Community Edition server on Docker Compose, loaded with 3M generated rows for analytics, EXPLAIN plans, transactions, CSV and vectors.

CedarDB has no cluster or replication mode yet (high availability is an announced
Enterprise Edition feature), so there is no multi-node example.

## Benchmark

pgbench + analytic queries on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-09-28): 3M orders load in 4.9 s, and most analytic queries over them take 1–100 ms (exact percentiles ~1.3 s); select-only 21.7k TPS with 16 clients. TPC-B tops out around 1.45k TPS: at scale 10 the hot branch rows cause repeatable-read serialization retries on 40–60% of transactions once there are 8+ clients. Full tables and method: [`single-node/README.md`](single-node/README.md#benchmark).
