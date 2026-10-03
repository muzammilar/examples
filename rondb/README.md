# RonDB

Website: https://www.rondb.com/

- [`single-node/`](single-node) — the minimal footprint: 1 management server, 1 data node (`NoOfReplicas=1`, `NumCPUs=1`, `DataMemory=256M`) and 1 MySQL Server, ~1.5 GB in total, no redundancy.
- [`docker-compose-cluster/`](docker-compose-cluster) — the minimal cluster from `rondb-docker`: 1 management server, 2 data nodes (1 node group, 2 replicas), 1 MySQL Server and the REST API server, with a `make failover` that stops a data node.

RonDB always runs as separate processes (management server, data nodes, MySQL Server), so
`single-node/` is still three containers: one of each, with one replica. It is the smallest
footprint, not a fault-tolerant setup: when its data node stops, every NDB query fails until it
restarts.

## Benchmark

sysbench through `mysqld` and wrk against the REST API, 6 CPUs / 12 GB split across the cluster (data nodes 1.625 CPU each, mysqld 2, rest 0.5; Apple M4 Pro, Docker VM aarch64, 2026-09-28): SQL point selects 19k/s at p99 0.47 ms, `oltp_read_only` 912 tps and `oltp_read_write` 712 tps (4–6 ms p50, ~55 ms p99), REST pk-reads 7.1k/s. Primary-key access is fast, and range and aggregate queries pay for data-node round trips. `mysqld` is the bottleneck under this budget. Full table and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).

Single node, sysbench through `mysqld` with 1 data node (`NumCPUs=1`, `DataMemory=256M`), no CPU caps, 2 × 50,000 rows, 8 threads, 30 s per workload (Apple M4 Pro, Docker VM aarch64, 2026-10-03): point selects 28.9k/s (p99 0.86 ms), `oltp_read_only` 1,809 tps and `oltp_read_write` 1,358 tps (p99 7.7 / 10.5 ms). `mysqld` used ~380% CPU and the data node's one thread ~84%. With no CPU caps and no second replica, these are not comparable with the cluster numbers above. Full table: [`single-node/README.md`](single-node/README.md#benchmark).

## Known issues

- `single-node/`: `TotalMemoryConfig` has a 2 GB floor (`Illegal value 1G for parameter TotalMemoryConfig. Legal values are between 2147483648 and 70368744177664`), so the smallest data node sets its memory pools by hand (`AutomaticMemoryConfig=false`). Details: [`single-node/README.md`](single-node/README.md#known-issues).
