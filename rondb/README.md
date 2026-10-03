# RonDB

Website: https://www.rondb.com/

- [`docker-compose-cluster/`](docker-compose-cluster) — the minimal cluster from `rondb-docker`: 1 management server, 2 data nodes (1 node group, 2 replicas), 1 MySQL Server and the REST API server, with a `make failover` that stops a data node.
- [`online-scaling/`](online-scaling) — what RonDB scales online: 2 → 4 data nodes (activate prepared nodes, `CREATE NODEGROUP`, `REORGANIZE PARTITION` under load), more MySQL / REST API servers, `DataMemory` and threads via rolling restart. Scaling in a node group that holds data is not supported.

There is no single-node example: RonDB always runs as separate processes (management
server, data nodes, MySQL Server). rondb-docker's smallest `mini` profile still starts
those processes with one data node (`NoOfReplicas=1`), which only drops the redundancy
this cluster demonstrates.

## Benchmark

sysbench through `mysqld` and wrk against the REST API, 6 CPUs / 12 GB split across the cluster (data nodes 1.625 CPU each, mysqld 2, rest 0.5; Apple M4 Pro, Docker VM aarch64, 2026-09-28): SQL point selects 19k/s at p99 0.47 ms, `oltp_read_only` 912 tps and `oltp_read_write` 712 tps (4–6 ms p50, ~55 ms p99), REST pk-reads 7.1k/s. Primary-key access is fast, and range and aggregate queries pay for data-node round trips. `mysqld` is the bottleneck under this budget. Full table and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).

Online scaling, sysbench `oltp_read_write`, 16 threads, 4 × 50,000 rows, data nodes and MySQL Servers at 2 CPUs each (2026-10-03): 1,077 tps with 2 data nodes and 1 MySQL Server, 845 tps after growing to 4 data nodes (the single MySQL Server stays the bottleneck), 1,651 tps with a second MySQL Server. The four `REORGANIZE PARTITION`s (~8 s each) stalled writes to 0–22 tps with p99 ~7.9 s, and throughput recovered right after. A rolling restart to `DataMemory` 1G / 3 threads per node kept 1,540–1,662 tps in every 5 s interval. Details: [`online-scaling/README.md`](online-scaling/README.md#benchmark).

## Known issues

- `online-scaling/`: `DROP NODEGROUP` only works on an empty node group, and with data it answers `1006: Illegal reply from server` / `error: -2`. RonDB cannot remove a node group that holds data ([docs](https://docs.rondb.com/rondb_mgm_client/), [Helm chart](https://github.com/logicalclocks/rondb-helm/blob/v26.2.20/templates/topology-immutability.yaml)). `REORGANIZE PARTITION` stalls writes while it runs, and a 4 × 16 MB redo log overloads during `sysbench prepare` (`Got temporary error 410 'REDO log files overloaded ...'`). Details: [`online-scaling/README.md`](online-scaling/README.md#known-issues).
