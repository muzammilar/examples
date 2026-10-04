# Tarantool — 3-instance replicaset as a StatefulSet on kind

The [`../docker-compose-cluster`](../docker-compose-cluster) replicaset (Tarantool 3.8.1, Raft
leader election, synchronous space) as a plain StatefulSet on a one-node kind cluster, with
Jobs for the walkthrough and the load, and `make failover` force-deleting the leader pod.

No operator or Helm chart is used: the CE operator only manages Cartridge clusters and is
inactive (see [Known issues](#known-issues)).

## Quick start

```bash
make up        # kind cluster, ConfigMaps, StatefulSet, wait for rollout and a leader
make test      # jobs/test.yaml: replication links, 1,000 sync writes, same rows on every pod
make failover  # jobs/load.yaml writes; the leader pod is force-deleted after 10 s
make status    # pods; per instance: election state, term, leader, replicated position, rows
make cli       # tt connect to the leader pod
make down      # kind delete cluster
```

`kind`, `kubectl` come from the repo's dev shell (`nix develop` / direnv).

## Setup

| object | what |
|---|---|
| [`kind-config.yaml`](kind-config.yaml) | one control-plane node, cluster `tarantool` |
| ConfigMap `tarantool-config` | [`app/config.yaml`](app/config.yaml), [`app/init.lua`](app/init.lua) (from `make cluster`) |
| ConfigMap `tarantool-scripts` | [`scripts/`](scripts) (same files as `../docker-compose-cluster/scripts`) |
| Service `tarantool` | headless, `publishNotReadyAddresses: true`: DNS names `tarantool-N.tarantool` exist while the instances bootstrap |
| StatefulSet `tarantool` ([`tarantool.yaml`](tarantool.yaml)) | 3 replicas, `podManagementPolicy: Parallel`, image `tarantool/tarantool:3.8.1`, `TT_INSTANCE_NAME` = pod name, readiness = the image's `status` script, 1 Gi PVC per pod, memory limit 1 Gi |
| Jobs [`test`](jobs/test.yaml), [`load`](jobs/load.yaml) | `tarantool` image running `scripts/run.lua` / `scripts/load.lua` (4 writers, 40 s) |

Config differences from the compose example:

| setting | here | why |
|---|---|---|
| instance names | `tarantool-0..2` | pod names, so `TT_INSTANCE_NAME` comes from `metadata.name` |
| `iproto.listen` | `0.0.0.0:3301` on every instance | the pod IP changes on restart |
| `iproto.advertise.peer.uri` | `tarantool-N.tarantool:3301` per instance | stable headless-Service name for replication |
| `replicas` | 3, fixed | each instance must be listed in `app/config.yaml`; scaling the StatefulSet alone would start `tarantool-3`, which is not in the config (not run) |

## Failover

`make failover`: [`load.lua`](scripts/load.lua) with 4 writers on the synchronous `ledger`
space; after 10 s `kubectl delete pod <leader> --force --grace-period=0`; the StatefulSet
recreates the pod with the same name and PVC.

2026-10-04, Apple M4 Pro, Docker VM aarch64, kind v0.32.0 (one node), one run:

| | |
|---|---|
| leader deleted | `tarantool-2` |
| new leader seen | `tarantool-1`, 1.2 s after the delete (polling `box.info.election.state`) |
| deleted pod recreated and Ready | 1.4 s after the delete (`kubectl wait --for=condition=Ready`) |
| longest stall for a writer | 0.42 s |
| failed attempts (all retried) | 4, `Can't modify data on a read-only instance - state is election follower with term N, synchro queue with term N belongs to N (…) and is frozen until promotion` |
| acknowledged writes | 348,504 (8,713/s over 40 s, 4 writers) |
| acknowledged writes lost | 0 |
| rows afterwards | 348,504 on each of the 3 pods |

Compose example for comparison (16 writers, `docker kill -s KILL`): new leader after 3.2 s,
longest stall 3.29 s. Here the stall was 0.42 s (7.8x shorter); one run each, not investigated.

## Known issues

- **No maintained CE operator or Helm chart for Tarantool 3.** `tarantool/tarantool-operator`
  ("Tarantool Operator manages Tarantool Cartridge clusters atop Kubernetes") last released
  `v1.0.0-rc3` on 2023-08-04, last push 2024-06-21; `tarantool/helm-charts` last released
  `tarantool-operator-1.0.1` / `cartridge-1.0.1` on 2023-05-05 (GitHub, checked 2026-10-04). Its
  README points Enterprise users to the
  [Tarantool Operator Enterprise](https://www.tarantool.io/ru/kubernetesoperator) for rolling
  updates and scaling down.
- On first `make up` one pod restarted once with `failed to create directory
  /var/lib/tarantool/sys_env/k8s/tarantool-2: … fio: No space left on device`: the shared Docker
  VM's disk was full at the time.
- `scripts/` is a copy of `../docker-compose-cluster/scripts` (ConfigMaps cannot reference files
  outside the folder via `--from-file` without a copy).

## Links

- Tarantool 3 configuration: https://www.tarantool.io/en/doc/latest/reference/configuration/configuration_reference/
- Leader election: https://www.tarantool.io/en/doc/latest/platform/replication/repl_leader_elect/
- tarantool-operator (Cartridge): https://github.com/tarantool/tarantool-operator
