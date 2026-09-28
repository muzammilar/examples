# SingleStore

Website: https://www.singlestore.com/

SingleStore (formerly MemSQL) is a distributed, MySQL wire-compatible SQL database: aggregators
plan queries and merge results, leaves store hash-sharded partitions in rowstore (in-memory)
or columnstore ("universal storage", the default) tables.

- [`single-node/`](single-node) — the official SingleStore Dev Image (a master aggregator + one leaf in one container) on Docker Compose; no license key needed.
- [`kubernetes-operator/`](kubernetes-operator) — 1 master aggregator + 1 child aggregator + 2 leaves on kind with the SingleStore Kubernetes Operator; needs a license key (`SINGLESTORE_LICENSE`).

SingleStore only publishes amd64 images; on Apple silicon both examples run under emulation.

## Benchmark

sysbench + columnstore queries on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, **amd64 image under Rosetta emulation**, 2026-09-28): rowstore point selects 28k/s at p99 0.35 ms; `oltp_read_only` 1,156 tps (18.5k qps) and `oltp_read_write` 940 tps with a ~54 ms p99 tail; warm columnstore aggregates over 600k rows take 50–190 ms, and a sort-key-pruned range 3.8 ms. Emulation understates it, so compare against native x86-64 before drawing conclusions. Full tables and method: [`single-node/README.md`](single-node/README.md#benchmark).
