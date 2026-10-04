# RisingWave

Website: https://risingwave.com/ · GitHub: https://github.com/risingwavelabs/risingwave

Streaming database (Postgres wire protocol): sources and tables feed materialized views that are
maintained incrementally; state lives in Hummock, an LSM tree on object storage. RisingWave 3.1.0.

| Folder | What |
|---|---|
| [`single-node/`](single-node) | `single_node` mode (all components in one process, local filesystem state store) on Docker Compose; psql walkthrough of tables, a datagen source, join/aggregate MVs, `EMIT ON WINDOW CLOSE`, sink into table and a native Postgres sink |
| [`docker-compose-cluster/`](docker-compose-cluster) | meta + 2 compute + compactor + frontend, Postgres meta store, MinIO state store; `make failover` kills a compute node under load (acknowledged rows lost with and without `implicit_flush`); `make scale-out` / `scale-in` 2 → 4 → 2 compute nodes with adaptive parallelism |
| [`kubernetes-operator/`](kubernetes-operator) | risingwave-operator v0.18.0 on kind: `RisingWave` resource with 1 meta, 2 compute, 1 compactor, 1 frontend, Postgres meta store, MinIO state store; one MV test |

## Benchmark summary

Apple M4 Pro, Docker VM aarch64. Full numbers in each example.

| Example | Date | Setup | Result |
|---|---|---|---|
| [single-node](single-node/README.md#results) | 2026-10-04 | one container, 4 CPUs / 8 GB | healthy 5.3 s after `make up`; walkthrough (10 streaming jobs) 14.4 s; 318 MiB resident |
| [docker-compose-cluster](docker-compose-cluster/README.md#results) | 2026-10-04 | compute nodes 2 CPUs / 4 GB, Go loader + datagen source | compute-node kill: writes failed 40 s (until the node returned), reads never failed; 5,400 of 1.68M acknowledged rows lost by default, 0 with `implicit_flush`. Aggregate throughput 0.86M → 1.90M rows/s from 2 to 4 compute nodes, rescheduled in 7 s; 4 → 2 in 4 s with `unregister-workers`; one failed write per change |
| [kubernetes-operator](kubernetes-operator/README.md#results) | 2026-10-04 | one kind node, compute pods 2 CPU / 3Gi | empty kind → `Running` RisingWave in ~3 min (image side-load 105 s, cert-manager 12 s, operator 17 s, cluster 47 s); node 2.15 GiB idle |

## Known issues

RisingWave 3.1.0, 2026-10-04.

- **Image size**: `risingwavelabs/risingwave:v3.1.0` is 2.52 GB compressed and 11.2 GB unpacked
  in the Docker VM.
- **Free license limit**: the Community Edition loads a default license
  (`rw-default-all-4-core`, `rwu_limit: Some(4)`) that enables paid features for clusters up to
  4 RWU (4 CPU cores, 16 GiB). Streaming parallelism is shown as `bounded(4)`.
  [Premium edition](https://docs.risingwave.com/get-started/rw-premium-edition-intro).
- `single_node` panics at a 6 GB memory limit (compactor memory assertion); 8 GB works
  ([details](single-node/README.md#known-issues)).
- `EMIT ON WINDOW CLOSE` and `APPEND ONLY` tables are marked experimental.

  (`rw-default-all-4-core`, `rwu_limit: Some(4)`) that enables paid features up to 4 RWU (4 CPU
  cores, 16 GiB). In the cluster it is not effective (37 cores counted) and paid features such as
  `DatabaseFailureIsolation` are off. [Premium edition](https://docs.risingwave.com/get-started/rw-premium-edition-intro).
- **DML durability**: INSERT returns before the rows are checkpointed. A compute-node crash lost
  acknowledged rows; `SET implicit_flush = true` (or `FLUSH`) avoids it at ~10x lower write
  throughput per connection ([details](docker-compose-cluster/README.md#failover)).
- A stopped compute node blocks all streaming jobs (recovery retries every ~3 s) until it returns or
  meta drops it after 60 s without heartbeat; remove nodes with `risingwave ctl meta unregister-workers`.
