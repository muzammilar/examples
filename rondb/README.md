# RonDB

Website: https://www.rondb.com/

- [`docker-compose-cluster/`](docker-compose-cluster) — the minimal cluster from `rondb-docker`: 1 management server, 2 data nodes (1 node group, 2 replicas), 1 MySQL Server and the REST API server, with a `make failover` that stops a data node.
- [`kubernetes-helm/`](kubernetes-helm) — the official RonDB Helm chart (26.2.20) on a one-node kind cluster: 1 management server, 2 data nodes, 1 MySQL Server, 1 REST API server; sysbench load while a data node pod is force-deleted.

There is no single-node example: RonDB always runs as separate processes (management
server, data nodes, MySQL Server). rondb-docker's smallest `mini` profile still starts
those processes with one data node (`NoOfReplicas=1`), which only drops the redundancy
this cluster demonstrates.

## Benchmark

sysbench through `mysqld` and wrk against the REST API, 6 CPUs / 12 GB split across the cluster (data nodes 1.625 CPU each, mysqld 2, rest 0.5; Apple M4 Pro, Docker VM aarch64, 2026-09-28): SQL point selects 19k/s at p99 0.47 ms, `oltp_read_only` 912 tps and `oltp_read_write` 712 tps (4–6 ms p50, ~55 ms p99), REST pk-reads 7.1k/s. Primary-key access is fast, and range and aggregate queries pay for data-node round trips. `mysqld` is the bottleneck under this budget. Full table and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).

Kubernetes (Helm chart on kind), sysbench `oltp_read_write` through the chart's `mysqld` Service, 8 threads, 4 × 50,000 rows, 120 s, data node pod `node-group-0-1` force-deleted 30 s in (2026-10-03): 752 tps / 15.0k qps, p99 51 ms. Every 5 s interval stayed at 730–767 tps, 8 transactions failed, and the data node was `started` again 32 s after the kill (node restart, 18 s). Details: [`kubernetes-helm/README.md`](kubernetes-helm/README.md#failover).

## Known issues

- `kubernetes-helm/`: the chart refuses to change `clusterSize.numNodeGroups` after install (`ERROR: clusterSize.numNodeGroups is immutable (deployed=1, requested=2)`), and `kind load docker-image` fails on Docker Desktop (`ctr: content digest sha256:...: not found`). Details: [`kubernetes-helm/README.md`](kubernetes-helm/README.md#known-issues).
