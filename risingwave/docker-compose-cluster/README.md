# RisingWave — Docker Compose cluster

Distributed RisingWave 3.1.0 on Docker Compose: one meta node, two compute nodes, a compactor and
a frontend, with metadata in Postgres and the Hummock state store in MinIO. `make failover` kills
a compute node under write load; `make scale-out` / `make scale-in` go from 2 to 4 compute nodes
and back while a source runs at full speed.

## Quick start

```bash
make up         # start everything and wait for healthchecks (~12 s)
make test       # sql/0*.sql: 200k-row join/aggregate MV checked against the batch query, actor placement
make failover   # SIGKILL compute-2 under load for 30 s, restart it, count acknowledged rows
make scale-out  # add compute-3 and compute-4 (2 -> 4) under load
make scale-in   # unregister and stop them (4 -> 2) under load
make status     # containers and rw_worker_nodes
make cli        # psql on the frontend
make down       # remove containers, volumes, network and the loader image
```

## Setup

| Service | Image | Port | Role | Limits |
|---|---|---|---|---|
| `postgres` | `postgres:17-alpine` | internal | meta store (`--backend sql`) | — |
| `minio` + `minio-bucket` | `cgr.dev/chainguard/minio` (pinned by digest) | `127.0.0.1:9401` console | Hummock state store, bucket `hummock001` | — |
| `meta` | `risingwavelabs/risingwave:v3.1.0` | `127.0.0.1:5691` dashboard | cluster metadata, barriers, scheduling, recovery | 1 GB |
| `compute-1`, `compute-2` | same | internal | streaming and batch actors, `--parallelism 2` | 2 CPUs, 4 GB (`--total-memory-bytes` 3 GiB) |
| `compute-3`, `compute-4` (profile `scale`) | same | internal | started by `make scale-out` | same |
| `compactor` | same | internal | LSM compaction of Hummock | 2 GB |
| `frontend` | same | `127.0.0.1:4566` | Postgres wire protocol (user `root`, no password) | 2 GB |
| `psql`, `loader` (profile `tools`) | `postgres:17-alpine`, built from [`loader/`](loader) | — | client; Go (pgx) writer/reader for failover and scaling | — |

The layout follows `docker/docker-compose-distributed.yml` in the RisingWave repo, without
Prometheus, Grafana and Redpanda. Compute nodes have `restart: "no"` so `make failover` controls
when they come back.

## What it does

| Target | Steps |
|---|---|
| `test` | [`01-schema-and-mvs.sql`](sql/01-schema-and-mvs.sql): 1000 customers, 200k orders, a join + aggregate MV, an assertion (division by zero on mismatch) that the MV equals the same query run as a batch query, an `UPDATE` that moves customers between groups. [`02-placement.sql`](sql/02-placement.sql): actors per compute node (16 each) and job parallelism |
| `failover` | [`scripts/failover.sh`](scripts/failover.sh): the loader writes 100-row batches with consecutive ids into two tables, `events_no_flush` (default) and `events_flush` (`SET implicit_flush = true`: INSERT returns after the checkpoint), retrying failed batches, and reads an MV every 100 ms. After 15 s `docker kill` compute-2, 30 s later `docker start` it. Then compares acknowledged ids with the table and the MV |
| `scale-out`, `scale-in` | [`scripts/scale.sh`](scripts/scale.sh): a `datagen` source (8 splits, 50M rows/s limit, so CPU-bound) aggregated by an MV; throughput = growth of `sum(count)` over 20 s before and after, and over the change itself; the loader runs at the same time. Scale-out starts compute-3/4; scale-in runs `risingwave ctl meta unregister-workers --workers compute-3:5688,compute-4:5688` and stops them. Parallelism is adaptive (default), so actors are rescheduled without any `ALTER ... SET PARALLELISM` |

## Results

2026-10-04, Apple M4 Pro, Docker VM aarch64 (11 CPUs, 24 GB), compute nodes at 2 CPUs / 4 GB each,
one run each.

### Failover

compute-2 killed (SIGKILL) at +15 s, started again at +47 s (healthy after 2 s).

| Measured | Value |
|---|---|
| writes (both tables) failing | +14.8 s to +54.7 s: 40 s, i.e. the whole time the node was down plus ~8 s |
| error while the node was down | `DML is not permitted during cluster recovery (no available table reader in streaming executors)` |
| meta recovery | `GLOBAL_RECOVERY_FAILURE` every ~3 s while the node was down; `GLOBAL_RECOVERY_SUCCESS` 7 s after the restart |
| reads of the MV during the outage | 0 failures (873 reads, max 32 ms): batch queries were served from the state in MinIO |
| acknowledged rows lost, default | **5,400 of 1,683,800** (54 batches): INSERT returned, the rows were not yet in a checkpoint, recovery rolled back to the last checkpoint |
| acknowledged rows lost, `implicit_flush = true` | 0 of 151,600 |
| MV count = table count after recovery | yes, both tables |
| write throughput before the kill | ~36k rows/s default, ~3.4k rows/s with `implicit_flush` (each INSERT waits for a ~1 s barrier) |

The cluster did not continue on compute-1 alone: actors stay assigned to compute-2 until it
returns, or until meta drops it after `max_heartbeat_interval_secs` (60 s), which this run did not
reach.

### Scaling (2 → 4 → 2 compute nodes)

| Step | Command | Rescheduled after | Throughput before | during | after | Failed loader requests |
|---|---|---|---|---|---|---|
| scale-out | `docker compose --profile scale up --detach --wait compute-3 compute-4` | 7 s (actors 33/33 → 24/24/24/26) | 0.86M rows/s | 1.00M rows/s | 1.90M rows/s | 1 write (0.3 s gap), 0 reads |
| scale-in | `risingwave ctl meta unregister-workers ...` + `docker stop` | 4 s (24/24/24/26 → 34/32) | 1.94M rows/s | 0.73M rows/s | 0.77M rows/s | 1 write per table (0.2–0.4 s gap), 0 reads |

- Throughput of the CPU-bound aggregate scaled 2.2x from 2 to 4 compute nodes.
- Each reschedule is a short stop of the streaming graph: in-flight DML fails once with the
  recovery error above and succeeds on retry.
- A first scale-out run with the source at 1M rows/s measured 0.98M → 1.00M rows/s: the source's
  own rate limit, not the cluster.
- Without `unregister-workers`, stopping a compute node is a failure (see failover): recovery
  stalls until it returns or meta drops it.

## Known issues

- Acknowledged DML is lost on a compute-node crash unless `implicit_flush` is on (or the client
  runs `FLUSH`): see above. [Fault tolerance](https://docs.risingwave.com/reference/fault-tolerance).
- The free license (4 RWU) is "not effective" in this cluster:
  `feature DatabaseFailureIsolation is not available due to license error: a valid license key is set, but it is currently not effective because the CPU core in the cluster (37) exceeds the maximum allowed by the license key (4)`.
  Compute nodes report 2 cores (their `cpus` limit) but meta, frontend and compactor report the
  Docker VM's 11. Paid features are off; the Community Edition features used here still work.
- The first failover run was invalid: the shared Docker VM disk filled up (`No space left on device`,
  MinIO HTTP `507`), and recovery looped (`GLOBAL_RECOVERY_START`/`SUCCESS` every ~50 ms). Leave
  a few GB free in the Docker VM.

## Links

- [Docker Compose deployment](https://docs.risingwave.com/deploy/risingwave-docker-compose)
- [Cluster scaling](https://docs.risingwave.com/deploy/k8s-cluster-scaling)
- [Fault tolerance](https://docs.risingwave.com/reference/fault-tolerance)
