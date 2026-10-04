# Manticore Search — official Helm chart on kind

The official chart `manticoresearch/manticoresearch` 29.9.1 (Manticore 29.9.0.1) on a one-node
kind cluster: 3 workers in one replication cluster plus the read balancer; failover of a worker
pod and scaling 3 → 5 → 3 workers, both under a Go load that counts every acknowledged row.
There is no Manticore Kubernetes operator; the chart is the official deployment.

## Quick start

```bash
make up         # kind cluster, load-client image, helm upgrade --install --wait (295–326 s)
make test       # cluster state; table on worker-0 added to the cluster; write via Service, read on every worker and via the balancer
make failover   # load on worker-0..2; force-delete worker-1; count every acknowledged row on every worker
make scale-out  # kubectl scale statefulset to 5 workers under load (RATE=1000 rows/s)
make scale-in   # back to 3 workers under load
make status     # pods, services, PVCs, cluster state per worker
make cli        # mysql client on worker-0
make down       # kind delete cluster, remove the load-client image
```

`kind`, `kubectl`, `helm` come from the repo's dev shell (`nix develop`); versions used:
kind 0.32.0 (node image `kindest/node:v1.36.1`), kubectl 1.37.1, Helm 4.3.0.

## Setup

| object | from the chart | notes |
|---|---|---|
| StatefulSet `manticore-manticoresearch-worker` | `manticoresearch/helm-worker:29.9.0.1` (arm64 native), 3 replicas | `replicationMode: multi-master`; cluster `manticore_cluster`; PVC `data-…` 1Gi each (chart default 10Gi); requests 250m / 256Mi, memory limit 2Gi (chart: none) |
| Deployment `manticore-manticoresearch-balancer` | `manticoresearch/helm-balancer:29.9.0.1` | builds a distributed table over the workers per table (`table_ha_strategy: nodeads`); reads only |
| Service `…-worker-svc` | ClusterIP over all workers | reads and writes |
| Service `…-worker-replication-svc` | headless | pod DNS names; the cluster registers nodes by these names |
| Service `…-balancer-svc` | ClusterIP | reads |
| Pod `load` | [`client/`](client) (Go, `go-sql-driver/mysql`), built locally and imported into the node | started by `make failover` / `make scale-*` |

- Only the overrides are in [`values.yaml`](values.yaml). The chart images are pulled by the
  kind node; the load image is imported with `docker save --platform linux/arm64 | ctr images import`.
- Each worker runs `searchd` plus the chart's PHP script (`replica.php`) under supervisord: on
  start it creates the cluster (first pod) or joins the oldest running pod.

## What `make test` shows

[`scripts/test.sh`](scripts/test.sh), 2026-10-04:

| step | result |
|---|---|
| cluster state | 3 workers `primary / synced`, size 3; `nodes_set` lists the pods by headless-Service DNS name |
| `CREATE TABLE logs …` on worker-0 | not added to the cluster by the chart: `autoAddTablesInCluster` only runs when a worker starts (`replica.php`); the script runs `ALTER CLUSTER manticore_cluster ADD logs` |
| `INSERT INTO manticore_cluster:logs` through `…-worker-svc` | 3 rows on each of the 3 workers |
| `SELECT … MATCH('timeout \| login')` through `…-balancer-svc` | rows 1 and 2 with `HIGHLIGHT()`; `SHOW TABLES` on the balancer: `logs` `distributed` (it appeared within the 5 s sync interval) |

## Failover

[`scripts/failover.sh`](scripts/failover.sh): load pod with 4 writers (`REPLACE` batches of 200
rows into `manticore_cluster:logs`, round-robin over the 3 worker pods, a failed batch retried
on the next pod until it succeeds) and 4 readers (full-text + filter + `GROUP BY`), 60 s, no
rate limit. At 15 s: `kubectl delete pod …-worker-1 --force --grace-period=0`.

2026-10-04, one run, Apple M4 Pro, Docker VM aarch64, kind on Docker Desktop, other agents
idle (shared Docker lock), memory limit 1Gi at the time:

| measure | value |
|---|---|
| writes before the kill | 81,473–89,961 rows/s |
| worker-0 sees cluster size 2 | 2.8 s after the delete |
| writes after the kill | 2,900 rows/s (16–18 s), 10,800–15,102 rows/s (18–24 s), 25,399 rows/s (24–26 s), 72,026 rows/s (26–28 s) |
| new worker-1 pod `Ready` (`synced`, size 3) | 11.0 s after the delete |
| failed attempts, all retried | 234 writes (205 `connect: connection refused`, 24 `cluster 'manticore_cluster' is not ready`), 244 reads |
| rows acknowledged / found on each worker | 3,788,600 / 3,788,600 on all 3, **0 lost** |

## Scale out and in (3 → 5 → 3)

[`scripts/scale.sh`](scripts/scale.sh): `kubectl scale statefulset …-worker --replicas=5` (then
`=3`) with the load running (4 writers paced to 1,000 rows/s total, `RATE`; 4 unpaced readers;
clients use worker-0..2 only). Fresh install (`make down && make up && make test`), 2026-10-04,
one run each:

| measure | scale-out 3 → 5 | scale-in 5 → 3 |
|---|---|---|
| time until cluster size and ready replicas match | 148.4 s (worker-3 and worker-4 start one after the other, each joins and copies `logs` by state transfer) | 4.9 s |
| writes (1,000 rows/s offered) | 1,000 rows/s before; no failed write during the step | 1,000 rows/s before and after; no failed write |
| reads | 13,281 q/s before → 5,750 q/s during (2.3x fewer); read p99 ≤ 2.8 ms | 1,850 q/s before → 1,495 q/s after; read p99 ≤ 8.0 ms |
| rows acknowledged / found | 160,200 / 160,200 on all 5 workers, **0 lost** | 46,000 / 46,000 on the 3 remaining workers, **0 lost** |

- Reads per second fall in both runs because `logs` grows the whole time (each query scans more).
- Scale-in leaves the removed pods in the cluster's node list:
  `cluster_manticore_cluster_nodes_set` still starts with `…-worker-3:9312,…-worker-4:9312`;
  `nodes_view` (live members) has only worker-0..2. The PVCs of worker-3 and -4 stay (StatefulSet
  default); a later scale-out reuses them.
- The load stopped at its 160 s limit 8 s before the scale-out step completed, so there is no
  "after" figure for scale-out.

## Known issues

Chart 29.9.1, Manticore 29.9.0.1, 2026-10-04.

- **Worker OOMKilled with a 1Gi memory limit.** After the unthrottled failover run (3.8M rows)
  a scale-out under unthrottled load took `logs` to ~10.5M rows; worker-0..2 were
  `OOMKilled` (exit 137) during the state transfer to worker-3, which never became ready, and
  the load failed. `values.yaml` now sets 2Gi and the scaling load is paced; the chart sets no
  limits by default.
- **`autoAddTablesInCluster` only acts at worker start**: a table created later stays local
  until `ALTER CLUSTER manticore_cluster ADD <table>`; writing it with the prefix before that
  fails (`table 'logs' is not in any cluster, use just 'logs'`).
- **`helm install --wait` can fail on a full disk**: with the Docker VM disk full, worker-1 never
  became ready (`FATAL: Requested size 134219048 for '/var/lib/manticore/galera.cache' exceeds available storage space 103714816: 28 (No space left on device)`,
  then `JOIN CLUSTER … replication init failed: 7 'error in node state, must reinit'` in a loop)
  and Helm stopped with `StatefulSet/manticore/manticore-manticoresearch-worker not ready … Replicas: 2/3`
  after `--timeout 10m`. Each worker preallocates a 128 MiB Galera cache.
- **Slow first start**: worker-0 waits 60 s (`Wait until …-worker-0 came alive`) before
  creating the cluster, and workers start one at a time: the `helm upgrade --install --wait` step took 295 s and 326 s in two installs.
- No auto-sharded tables here: the chart's `listen` order puts `9308:http` before
  `$hostname:9312`, which breaks Buddy's node id the same way as the Docker image default
  (see `docker-compose-cluster/` on branch `manticore-docker-compose-cluster`; not tried on the chart).

## Links

- Chart: https://github.com/manticoresoftware/manticoresearch-helm (tag `manticoresearch-29.9.1`), repo https://helm.manticoresearch.com
- Worker start-up script: https://github.com/manticoresoftware/manticoresearch-helm/blob/manticoresearch-29.9.1/sources/manticore-worker/replica.php
