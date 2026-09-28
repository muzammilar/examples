# FoundationDB — cluster on kind with the FoundationDB Kubernetes Operator

[fdb-kubernetes-operator](https://github.com/FoundationDB/fdb-kubernetes-operator) `v2.37.0`
managing a `double`-redundancy `FoundationDBCluster` on the Redwood engine (`ssd-redwood-1`)
([`cluster.yaml`](cluster.yaml), adapted from the operator's local-testing sample) on a one-node
kind cluster, with a separate pod per process class, plus
[foundationdb-exporter](https://github.com/aikoven/foundationdb-exporter) reading the
cluster file the operator publishes.

Requires `kind`, `kubectl`, `curl`, and `jq` + `column` for `make roles`.

```bash
make up       # kind cluster, CRDs + operator, FoundationDBCluster, exporter
make test     # run queries/test.fdbcli on a log pod, then `make roles`
make roles    # pods by process class + address/class/roles table from `status json`
make failover # delete one storage pod, wait until available + fully replicated, `make roles`
make status   # FoundationDBCluster + fdbcli `status details`
make cli      # interactive fdbcli
make metrics  # port-forward the exporter to localhost:9444
make down     # delete the kind cluster
```

`up` is split into `kind-cluster`, `preload`, `operator`, `cluster`, `exporter` if you want to go step
by step. `cluster` waits until the cluster is available and `.status.generations.reconciled` equals
`.metadata.generation` (the operator has applied the whole spec).

Besides the operator image, the operator sample's init containers pull
`foundationdb/fdb-kubernetes-monitor:7.3.69` and `:7.4.5` (to copy client libraries), and every
FDB pod uses `foundationdb/fdb-kubernetes-monitor:7.3.79` (the unified image, for both containers); pulling those inside kind is the slow part of `make up`.
`make preload` (run by `up` after `kind-cluster`) copies any of these that already exist in your
local Docker into the kind node (`docker save | ctr images import`) and skips the rest, so pull them
on the host first to speed things up:

```bash
for i in foundationdb/fdb-kubernetes-operator:v2.37.0 foundationdb/fdb-kubernetes-monitor:7.3.69 \
  foundationdb/fdb-kubernetes-monitor:7.4.5 foundationdb/fdb-kubernetes-monitor:7.3.79; do docker pull $i; done
docker pull --platform linux/amd64 aikoven/foundationdb-exporter:3.1.0
```
`faultDomain: foundationdb.org/none` lets all processes share the single kind node.

## Process classes and roles

`spec.processCounts` sets how many pods of each class the operator creates (one FDB process per
pod, labelled `foundationdb.org/fdb-process-class`); `databaseConfiguration` sets how many of each
role FDB recruits onto them (`logs: 2`, `commit_proxies: 1`, `grv_proxies: 1`, `resolvers: 1`).

| Class | Pods | Roles recruited there |
| ----- | ---- | --------------------- |
| `cluster_controller` | 1 | `cluster_controller` — elects and monitors every other role |
| `stateless` | 3 | `master` (sequencer), `commit_proxy`, `grv_proxy`, `resolver`, `ratekeeper`, `data_distributor`, `consistency_scan` |
| `log` | 3 | `log` (transaction log) on 2 of them, the third is a spare |
| `storage` | 3 | `storage` (key/value data, 2 replicas) |

The operator also picks 3 `coordinator`s from the log/storage pods.

`make test` runs the fdbcli queries, then prints `kubectl get pods -L foundationdb.org/fdb-process-class`
and a table built from `status json` with jq:

```
.cluster.processes[] | [.address, .class_type, ([.roles[].role] | join(","))]
```

so you can see which pod holds which role, e.g.:

```
ADDRESS           CLASS               ROLES
10.244.0.14:4501  cluster_controller  cluster_controller
10.244.0.18:4501  log                 coordinator,log
10.244.0.22:4501  log                 coordinator
10.244.0.23:4501  log                 coordinator,log
10.244.0.10:4501  stateless           grv_proxy
10.244.0.8:4501   stateless           commit_proxy,resolver
10.244.0.9:4501   stateless           master,data_distributor,ratekeeper,consistency_scan
10.244.0.16:4501  storage             storage
10.244.0.19:4501  storage             storage
10.244.0.21:4501  storage             storage
```

`status details` (from `make status`) shows the same `Desired ...` counts and storage engine. Stateless roles move between the stateless pods
after recoveries, so the exact placement changes from run to run.

## Failover

`make failover` deletes the first storage pod. The operator recreates it with the same name and
PVC (new pod IP), and its storage server rejoins with the data already on its volume. From the
delete on, the target polls `status json` every 2 s (bounded, 300 x 2 s) and prints each change of
data state, `healthy`, fault tolerance (`max_zone_failures_without_losing_data`) and the number of
processes holding a `storage` role, until the pod is Ready, the data is healthy, fault tolerance is
back to 1 and every storage pod runs a storage server again. It then waits for the
`FoundationDBCluster`'s `.status.health.available` and `.status.health.fullReplication` and ends
with `make roles`, where the replacement pod shows up under its new address, e.g.:

```
-- deleting pod/test-cluster-storage-19036 (3 storage pods)
SECONDS  DATA_STATE  HEALTHY  FAULT_TOLERANCE  STORAGE_SERVERS
8        healthy     true     1                3
```

On kind the pod is back within seconds, before data distribution gives up on the storage server,
so FDB typically does not need to re-replicate anything and the table may show only the healthy
end state. If the replacement took longer, the data state would first go through `healing`
(data distribution re-replicating the lost copies) and fault tolerance would drop to 0.

The kind node's disk is the Docker VM's disk. FDB throttles writes once less than 5% of it is free,
so `cluster.yaml` lowers that floor with `knob_min_available_space_ratio=0.01` (and 256 MiB absolute).
