# RonDB

Website: https://www.rondb.com/

- [`docker-compose-cluster/`](docker-compose-cluster) — the minimal cluster from `rondb-docker`: 1 management server, 2 data nodes (1 node group, 2 replicas), 1 MySQL Server and the REST API server, with a `make failover` that stops a data node.

There is no single-node example: RonDB always runs as separate processes (management
server, data nodes, MySQL Server). rondb-docker's smallest `mini` profile still starts
those processes with one data node (`NoOfReplicas=1`), which only drops the redundancy
this cluster demonstrates.

## Benchmark

sysbench through `mysqld` and wrk against the REST API, 6 CPUs / 12 GB split across the cluster (data nodes 1.625 CPU each, mysqld 2, rest 0.5; Apple M4 Pro, Docker VM aarch64, 2026-09-28): SQL point selects 19k/s at p99 0.47 ms, `oltp_read_only` 912 tps and `oltp_read_write` 712 tps (4–6 ms p50, ~55 ms p99), REST pk-reads 7.1k/s. Primary-key access is fast, and range and aggregate queries pay for data-node round trips. `mysqld` is the bottleneck under this budget. Full table and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).
