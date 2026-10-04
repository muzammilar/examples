# Memgraph — Kubernetes (official Helm chart, standalone)

One Memgraph Community pod from the official `memgraph/memgraph` Helm chart on a one-node kind
cluster: walkthrough through `kubectl exec` and through the Service, and a pod kill under write
load (the pod comes back on the same PVC and recovers from snapshot + WAL).

## Quick start

```bash
make up        # kind cluster, client image side-loaded, helm upgrade --install --wait
make test      # cypher/01-walkthrough.cypher in the pod, then a query through the Service from a client pod
make failover  # write Job, force-delete memgraph-0 at 10 s, wait for the new pod, verify every acknowledged write
make status    # pods, Service, PVCs, version, replication role
make cli       # mgconsole in memgraph-0
make down      # kind delete cluster, remove the client image
```

`kind`, `kubectl` and `helm` come from the repo's dev shell (`nix develop` / direnv).

## Setup

| item | value |
|---|---|
| kind cluster | `memgraph-helm`, 1 control-plane node ([`kind-config.yaml`](kind-config.yaml)) |
| chart | `memgraph/memgraph` 1.0.7 from `https://memgraph.github.io/helm-charts`, release `memgraph`, namespace `memgraph` |
| image | `docker.io/memgraph/memgraph:3.13.1` (chart appVersion, pinned in [`values.yaml`](values.yaml)) |
| workload | StatefulSet `memgraph`, 1 pod, Service `memgraph` (ClusterIP, Bolt 7687) |
| storage | PVC `memgraph-lib-storage-memgraph-0` 2Gi (chart default 10Gi), `memgraph-log-storage-memgraph-0` 256Mi (default 1Gi), kind's `standard` class |
| resources | requests 1 CPU / 2Gi, limits 2 CPU / 2Gi; `--memory-limit=1536` (MiB, below the pod limit, as the chart advises) |
| flags | chart defaults (`--data-directory=/var/lib/memgraph/mg_data`, `--also-log-to-stderr=true`) plus `--telemetry-enabled=false`, `--storage-wal-enabled=true`, `--storage-snapshot-interval-sec=60` |
| init container | chart's privileged `sysctl -w vm.max_map_count=524288` (default 262144) with `busybox:1.37` (default `busybox:latest`); on kind this sets the Docker VM's value |
| client | [`client/`](client) (Go, `neo4j-go-driver/v6` 6.3.0, same program as `docker-compose-cluster/client` without the read mode), side-loaded with `kind load docker-image`, run as Job [`jobs/load.yaml`](jobs/load.yaml) |

## What it does

- `make test`: index, 1,000 accounts, 5,000 `PAID` edges (count 1000 / 5000 / sum 127500), a
  2-hop count, `SHOW STORAGE INFO` (`vm_max_map_count` 524288, `memory_limit` 1.50GiB),
  `CREATE SNAPSHOT`; then `MATCH (a:Account) RETURN count(a)` from a throwaway pod through the
  Service (1000).
- `make failover`: Job `load` runs `client write` (4 workers, 40 s, one `CREATE (:Tick {seq})`
  per auto-commit query, retries a failed write) then `client verify`. At 10 s:
  `kubectl delete pod memgraph-0 --force --grace-period=0`.

## Results

2026-10-04, Apple M4 Pro, Docker VM aarch64 (11 CPUs, 24.4 GB), kind v0.32.0 (its default node image, `kindest/node@sha256:3489c767...`),
pod limits 2 CPU / 2Gi, one run, Docker used by this example only (shared lock).

| | result |
|---|---|
| writes before the kill | 16,363–18,368 writes/s (4 workers) |
| new pod `Ready` after the force delete | 5.5 s |
| longest gap between two acknowledged writes | 6,386 ms (from t=9.9 s); 0 writes/s at t=11–16 s, 13,211 at t=17 s |
| failed attempts (retried) | 65: 37 `connection refused`, 24 connection timeouts, 3 `EOF`, 1 `connection reset by peer` |
| after the restart | 16,523–17,438 writes/s |
| acknowledged writes | 569,739 in 40.0 s (14,242/s average) |
| lost acknowledged writes | 0 (569,739 `:Tick` nodes, 0 never acknowledged, 0 duplicates) |

A single instance has no failover: writes stop until the StatefulSet's new pod has recovered
from the PVC (snapshot + WAL). Kubernetes restarts it; nothing takes over meanwhile.

## Scaling

Not supported with this chart. `replicaCount` > 1 starts independent standalone instances, each
with its own data: no replication is configured between them. Replication on Kubernetes comes
with the `memgraph/memgraph-high-availability` chart (1.4.1), which runs the HA coordinators and
needs an Enterprise license (`Access to high availability requires an enterprise, ai_platform, or
oem license.` without one; see [`../README.md`](../README.md#license)). Not run here. Read
replicas with Community replication are shown in
[`../docker-compose-cluster`](../docker-compose-cluster).

## Known issues

- The chart's `helm test` hook Job (`memgraph-test`) uses `memgraph/memgraph:3.5.1`, not the
  chart's appVersion; it is not run here.
- The chart's sysctl init container sets `vm.max_map_count` unconditionally (it can lower a higher
  value) and is privileged.
- `terminationGracePeriodSeconds` is 1800 by default (time for a snapshot on exit); a plain
  `kubectl delete pod` can wait that long. `make failover` force-deletes.
- WAL fsync every 100,000 transactions by default (`--storage-wal-file-flush-every-n-tx`): the
  pod kill lost nothing because the WAL was in the node's page cache; a node crash can lose up to
  that many acknowledged transactions.

## Links

- Helm charts: https://github.com/memgraph/helm-charts
- Kubernetes docs: https://memgraph.com/docs/getting-started/install-memgraph/kubernetes
- Data durability: https://memgraph.com/docs/fundamentals/data-durability
