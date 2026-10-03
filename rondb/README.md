# RonDB

Website: https://www.rondb.com/

| folder | what |
|---|---|
| [`single-node/`](single-node) | Minimal footprint: 1 management server, 1 data node (`NoOfReplicas=1`, `NumCPUs=1`, `DataMemory=256M`), 1 MySQL Server; ~1.5 GB total, no redundancy |
| [`docker-compose-cluster/`](docker-compose-cluster) | Minimal cluster from `rondb-docker`: 1 management server, 2 data nodes (1 node group, 2 replicas), 1 MySQL Server, REST API server; `make failover` stops a data node |
| [`online-feature-store/`](online-feature-store) | Online feature serving (Hopsworks-style feature groups, 1M users x 2 tables) from a Go client: single-row SQL vs `IN` lists vs pushed join vs REST `pk-read` / `batch`, p50/p99 and throughput, serving through a data-node stop/restart |
| [`online-scaling/`](online-scaling) | Online scaling: 2 → 4 data nodes (activate prepared nodes, `CREATE NODEGROUP`, `REORGANIZE PARTITION` under load), more MySQL / REST API servers, `DataMemory` and threads via rolling restart. Scaling in a node group that holds data is not supported |
| [`kubernetes-helm/`](kubernetes-helm) | Official RonDB Helm chart (26.2.20) on a one-node kind cluster: 1 management server, 2 data nodes, 1 MySQL Server, 1 REST API server; sysbench load while a data node pod is force-deleted |

RonDB always runs as separate processes (management server, data nodes, MySQL Server), so
`single-node/` is still three containers with one replica. It is not fault tolerant: while its
data node is stopped, every NDB query fails.

## Benchmark

All on an Apple M4 Pro, Docker VM aarch64. Details and method in each example's README.

| example | date | setup | result |
|---|---|---|---|
| [cluster](docker-compose-cluster/README.md#benchmark) | 2026-09-28 | sysbench via `mysqld` + wrk via REST; 6 CPUs / 12 GB split (data nodes 1.625 CPU each, mysqld 2, rest 0.5) | point selects 19k/s, p99 0.47 ms; `oltp_read_only` 912 tps, `oltp_read_write` 712 tps (4–6 ms p50, ~55 ms p99); REST pk-reads 7.1k/s. `mysqld` is the bottleneck |
| [single node](single-node/README.md#benchmark) | 2026-10-03 | sysbench via `mysqld`, 1 data node (`NumCPUs=1`, `DataMemory=256M`), no CPU caps, 2 × 50,000 rows, 8 threads, 30 s per workload | point selects 28.9k/s (p99 0.86 ms); `oltp_read_only` 1,809 tps, `oltp_read_write` 1,358 tps (p99 7.7 / 10.5 ms); `mysqld` ~380% CPU, data node thread ~84%. Not comparable with the cluster (no caps, no second replica) |
| [feature store](online-feature-store/README.md#results) | 2026-10-02 | 16 clients, 16 users x 2 feature groups per vector, 1M users per group, no CPU limits, shared Docker VM | `IN (...)` 8.9k vectors/s (p50 1.4 / p99 6.7 ms); REST `batch` 7.1k/s (p50 1.7 / p99 9.7 ms); one round trip per row ~1k/s at 11–18 ms p50. Single user: REST `batch` 0.43 ms p50, 28k vectors/s. Data node stop: 0 failed requests (8 SQL reads retried) |
| [online scaling](online-scaling/README.md#benchmark) | 2026-10-03 | `oltp_read_write`, 16 threads, 4 × 50,000 rows, data nodes and MySQL Servers 2 CPUs each | 2 data nodes / 1 mysqld 1,077 tps; 4 data nodes 845 tps (single mysqld is the bottleneck); 2 mysqld 1,651 tps. Four `REORGANIZE PARTITION`s (~8 s each): 0–22 tps, p99 ~7.9 s, then recovered. Rolling restart to `DataMemory` 1G / 3 threads: 1,540–1,662 tps per 5 s interval |
| [Kubernetes](kubernetes-helm/README.md#failover) | 2026-10-03 | `oltp_read_write` via chart's `mysqld` Service, 8 threads, 4 × 50,000 rows, 120 s, pod `node-group-0-1` force-deleted at 30 s | 752 tps / 15.0k qps, p99 51 ms; every 5 s interval 730–767 tps; 8 failed transactions; data node `started` 32 s after the kill (node restart 18 s) |

## Known issues

- `single-node/`: `TotalMemoryConfig` has a 2 GB floor (`Illegal value 1G for parameter TotalMemoryConfig. Legal values are between 2147483648 and 70368744177664`), so the data node sets its memory pools by hand (`AutomaticMemoryConfig=false`). [Details](single-node/README.md#known-issues).
- `online-feature-store/` (RonDB 26.02.10):
  - Default redo log: bulk inserts fail with NDB error 410 ("REDO log files overloaded"), seen by the client as MySQL error 1297 (temporary). The loader retries. A larger redo log avoids it but makes each data-node volume ~1.2 GB.
  - A data node restarting while the Docker disk is full dies with error 2810 ("file system full") and does not rejoin.
  - Stopping a data node aborts its in-flight transactions. SQL reads get error 1205 ("Lock wait timeout exceeded") and must be retried by the client; REST `batch` reads saw no errors, only a ~1.3 s stall.
  - REST `feature_store` / `batch_feature_store` need the Hopsworks metadata database. On plain RonDB: `Database/Table does not exist. Database: hopsworks. Table: feature_store`.
  - `rdrs2` is CPU-bound on JSON. With `NumThreads` 4 it was the bottleneck for batch reads; the example uses 8.
- `online-scaling/`: `DROP NODEGROUP` only works on an empty node group; with data it answers `1006: Illegal reply from server` / `error: -2` ([docs](https://docs.rondb.com/rondb_mgm_client/), [Helm chart](https://github.com/logicalclocks/rondb-helm/blob/v26.2.20/templates/topology-immutability.yaml)). `REORGANIZE PARTITION` stalls writes while it runs. A 4 × 16 MB redo log overloads during `sysbench prepare` (`Got temporary error 410 'REDO log files overloaded ...'`). [Details](online-scaling/README.md#known-issues).
- `kubernetes-helm/`: the chart refuses to change `clusterSize.numNodeGroups` after install (`ERROR: clusterSize.numNodeGroups is immutable (deployed=1, requested=2)`); `kind load docker-image` fails on Docker Desktop (`ctr: content digest sha256:...: not found`). [Details](kubernetes-helm/README.md#known-issues).
