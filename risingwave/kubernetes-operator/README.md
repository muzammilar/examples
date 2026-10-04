# RisingWave — Kubernetes operator

The official [risingwave-operator](https://github.com/risingwavelabs/risingwave-operator) v0.18.0
on a one-node kind cluster, running a `RisingWave` resource (RisingWave 3.1.0: 1 meta, 2 compute,
1 compactor, 1 frontend) with a Postgres meta store and a MinIO state store, and one MV test.

## Quick start

```bash
make up      # kind cluster, image side-load, cert-manager, operator, Postgres + MinIO, RisingWave, psql pod
make test    # sql/01-mv.sql through psql: 100k-row aggregate MV, update, assertion, actors per compute pod
make status  # RisingWave resource, pods, services, rw_worker_nodes
make cli     # interactive psql on risingwave-frontend:4567
make down    # kind delete cluster
```

`up` runs `kind-cluster cert-manager operator cluster client`; each step is idempotent.

## Setup

| Component | Version / image | Notes |
|---|---|---|
| kind node | `kindest/node` from the repo's kind | one control-plane node ([`kind-config.yaml`](kind-config.yaml)) |
| cert-manager | v1.21.2 | serving certificates for the operator's webhooks |
| risingwave-operator | v0.18.0 (`risingwave-operator.yaml` from the release, `kubectl apply --server-side`) | namespace `risingwave-operator-system` |
| Postgres | `postgres:17-alpine`, emptyDir | meta store ([`backends.yaml`](backends.yaml)) |
| MinIO | `cgr.dev/chainguard/minio` pinned by digest, emptyDir | state store, bucket `hummock001` |
| RisingWave | `risingwavelabs/risingwave:v3.1.0` ([`risingwave.yaml`](risingwave.yaml)) | side-loaded into the node with `docker save --platform` + `ctr import` when present locally |

Resources in `risingwave.yaml`, lowered from the operator's examples (8 CPU / 32Gi per compute
node, 1 CPU / 2Gi for meta and frontend):

| Component | Replicas | Requests | Limits |
|---|---|---|---|
| meta | 1 | 250m, 1Gi | 1 CPU, 1Gi |
| frontend | 1 | 250m, 1Gi | 1 CPU, 1Gi |
| compactor | 1 | 250m, 2Gi | 1 CPU, 2Gi |
| compute | 2 | 500m, 3Gi | 2 CPUs, 3Gi |

The operator's examples use 10Gi / 100Gi PVCs for Postgres and MinIO; here they are emptyDir,
since `make down` deletes the cluster.

## Results

2026-10-04, Apple M4 Pro, Docker VM aarch64 (11 CPUs, 24 GB), one run.

| Step | Time |
|---|---|
| `kind-cluster` incl. side-loading the 2.5 GB image | 105 s (import 48.5 s) |
| `cert-manager` | 12 s |
| `operator` | 17 s |
| `cluster` (Postgres + MinIO ready, then `RisingWave` condition `Running`) | 47 s (RisingWave pods ready ~12 s after apply) |
| `test` | 3 s; MV equals the batch query; 8 actors on each compute pod |

| Measured | Value |
|---|---|
| kind node memory with the cluster idle after `test` | 2.15 GiB |
| `/var/lib/containerd` in the kind node | 13 GB (the RisingWave image unpacked again) |

Scaling and failover are shown in [`../docker-compose-cluster`](../docker-compose-cluster); with
the operator, compute nodes are scaled by changing `spec.components.compute.nodeGroups[].replicas`.

## Known issues

- The kind node needs ~13 GB of disk for the RisingWave image on top of the 11.2 GB copy in local
  Docker; delete the local image after `make up` if disk is short.
- The operator's install manifest is applied with retries: until the cert-manager webhook is
  serving, `kubectl apply` can fail with `failed calling webhook "webhook.cert-manager.io"`
  (operator README).

## Links

- [Deploy on Kubernetes with the operator](https://docs.risingwave.com/deploy/risingwave-kubernetes)
- [risingwave-operator](https://github.com/risingwavelabs/risingwave-operator) ·
  [example manifests](https://github.com/risingwavelabs/risingwave-operator/tree/main/docs/manifests)
