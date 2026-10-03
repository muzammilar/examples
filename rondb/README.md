# RonDB

Website: https://www.rondb.com/

- [`docker-compose-cluster/`](docker-compose-cluster) — the minimal cluster from `rondb-docker`: 1 management server, 2 data nodes (1 node group, 2 replicas), 1 MySQL Server and the REST API server, with a `make failover` that stops a data node.
- [`online-feature-store/`](online-feature-store) — online feature serving (Hopsworks-style feature groups, 1M users x 2 tables) from a Go client: single-row SQL vs `IN` lists vs a pushed join vs REST `pk-read` / `batch`, with p50/p99 and throughput, and serving through a data-node stop/restart.

There is no single-node example: RonDB always runs as separate processes (management
server, data nodes, MySQL Server). rondb-docker's smallest `mini` profile still starts
those processes with one data node (`NoOfReplicas=1`), which only drops the redundancy
this cluster demonstrates.

## Benchmark

sysbench through `mysqld` and wrk against the REST API, 6 CPUs / 12 GB split across the cluster (data nodes 1.625 CPU each, mysqld 2, rest 0.5; Apple M4 Pro, Docker VM aarch64, 2026-09-28): SQL point selects 19k/s at p99 0.47 ms, `oltp_read_only` 912 tps and `oltp_read_write` 712 tps (4–6 ms p50, ~55 ms p99), REST pk-reads 7.1k/s. Primary-key access is fast, and range and aggregate queries pay for data-node round trips. `mysqld` is the bottleneck under this budget. Full table and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).

Online feature serving, 16 clients each asking for 16 users x 2 feature groups out of 1M users per group (same hardware, no CPU limits, shared Docker VM, 2026-10-02): `WHERE user_id IN (...)` 8.9k vectors/s at p50 1.4 / p99 6.7 ms and one REST `batch` call 7.1k/s at p50 1.7 / p99 9.7 ms, against ~1k/s at 11-18 ms p50 for one round trip per row. For a single user, REST `batch` is fastest: 0.43 ms p50, 28k vectors/s. Stopping a data node mid-run failed no requests (8 SQL reads were retried). Details: [`online-feature-store/README.md`](online-feature-store/README.md#results).

## Known issues

Seen while building these examples (RonDB 26.02.10, 2026-10-02):

- With the default redo log, bulk inserts fail with NDB error 410 ("REDO log files overloaded"),
  which reaches the client as MySQL error 1297 (temporary). The example's loader retries. A
  larger redo log avoids it but makes each data-node volume ~1.2 GB.
- A data node that restarts while the Docker disk is full dies with error 2810 ("file system
  full") and does not rejoin.
- Stopping a data node aborts the transactions in flight on it. SQL reads get error 1205 ("Lock
  wait timeout exceeded") and must be retried by the client. REST `batch` reads saw no errors,
  only a ~1.3 s stall.
- The REST server's `feature_store` and `batch_feature_store` endpoints need the Hopsworks
  metadata database. On plain RonDB they answer `Database/Table does not exist. Database:
  hopsworks. Table: feature_store`.
- The REST server (`rdrs2`) is CPU-bound on JSON. With `NumThreads` 4 it was the bottleneck
  for batch reads, so the example uses 8.
