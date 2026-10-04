# Redpanda — Redpanda Operator on kind

Three Redpanda brokers managed by the official
[Redpanda Operator](https://docs.redpanda.com/current/deploy/redpanda/kubernetes/local-guide/)
on a 4-node kind cluster, with a pod-kill failover test.

## Quick start

```bash
make up        # kind cluster, operator (Helm), Redpanda resource; waits for Ready and all 3 pods (~2.5 min)
make test      # scripts/01-test.sh in redpanda-0: RF=3 topic, write via redpanda-0, read via redpanda-2
make failover  # rpk producer under acks=all, force-delete pod redpanda-2, read every acked record back
make status    # Redpanda resource, pods, PVCs, rpk cluster health
make cli       # bash in redpanda-0
make down      # kind delete cluster
```

Steps of `up`: `kind-cluster`, `operator`, `cluster` (each idempotent). `kind`, `kubectl` and
`helm` come from the repo's dev shell (`nix develop`).

## Setup

| piece | version / setting |
|---|---|
| kind | [`kind-config.yaml`](kind-config.yaml): 1 control plane + 3 workers (Kubernetes v1.36.1 node image of kind 0.32.0) |
| operator | chart `redpanda/operator` `26.2.4` (app v26.2.4), release `redpanda-controller` in namespace `redpanda`, `crds.enabled=true`, cluster-scoped |
| cluster | [`redpanda-cluster.yaml`](redpanda-cluster.yaml): `Redpanda` resource (`cluster.redpanda.com/v1alpha2`), image `redpanda:v26.2.3` (the chart default) |
| brokers | `statefulset.replicas: 3`, one per worker (the chart's default pod anti-affinity) |

Lowered or changed from the chart defaults, with the reason in the manifest:

| setting | here | default |
|---|---|---|
| `resources.cpu.cores` | 1 | 1 |
| `resources.memory.container.max` | 2Gi | 2.5Gi |
| `storage.persistentVolume.size` | 2Gi | 20Gi |
| `tls.enabled` | false (no cert-manager needed) | true |
| `external.enabled` | false (no NodePorts) | true |
| `console.enabled` | false (Console is in [`../single-node`](../single-node)) | true |
| `config.cluster.storage_min_free_bytes` | 1 GiB | 5 GiB: kind volumes live on the shared Docker VM disk |
| `config.cluster.partition_autobalancing_max_disk_usage_percent` | 99 | 80, same reason (see [`../docker-compose-cluster`](../docker-compose-cluster/README.md#scaling-3--5--3-brokers)) |

- Inside the cluster brokers are `redpanda-N.redpanda.redpanda.svc.cluster.local:9093` (Kafka)
  and `:9644` (Admin).
- License: the operator and a Redpanda cluster run without a key (the resource shows `Cluster
  has a valid license` during the built-in 30-day trial). The operator gates only Redpanda
  Connect pipelines and multi-cluster (stretch) deployments on an Enterprise license
  ([licensing overview](https://docs.redpanda.com/current/get-started/licensing/overview/)).

## Failover

[`scripts/failover.sh`](scripts/failover.sh): in `redpanda-0`, `rpk topic produce` (acks=all, the
rpk default, `--delivery-timeout 60s`) writes 30,000 records at ~500/s into a 6-partition RF=3
topic and prints each acknowledged value; after 10 s `kubectl delete pod redpanda-2 --force
--grace-period=0`; the StatefulSet recreates the pod on the same PVC. Then every acknowledged
value is looked up in a full read of the topic.

2026-10-04, Apple M4 Pro, Docker VM aarch64 (Docker 29.5.3), one run, nothing else running in
Docker:

| acked | read back | acked but missing | producer errors | pod Ready + cluster healthy after delete |
|---:|---:|---:|---:|---:|
| 30,000 of 30,000 | 30,000 | 0 | 0 | 23 s |

- rpk uses one sticky partition at a time, so this is a durability check, not a latency
  measurement; for stall times per partition see the
  [compose failover](../docker-compose-cluster/README.md#failover) (franz-go client).
- Scaling (3 → 5 → 3 with decommission) is in [`../docker-compose-cluster`](../docker-compose-cluster);
  it is not repeated here (5 brokers would need 5 kind workers with the default anti-affinity).

## Known issues

- `kubectl wait redpanda/redpanda --for=condition=Ready` returned while `redpanda-1` and
  `redpanda-2` were still `1/2` Ready; `make cluster` also waits for the three pods.
- `kubectl rollout status statefulset/redpanda` fails with `rollout status is only available for
  RollingUpdate strategy type`: the StatefulSet the operator creates is not `RollingUpdate`
  (the chart value is; the operator does its own rolling restarts). `make cluster` waits on the pods.

## Links

- [Deploy locally with kind](https://docs.redpanda.com/current/deploy/redpanda/kubernetes/local-guide/)
- [Redpanda Operator / Helm chart repo](https://github.com/redpanda-data/redpanda-operator)
