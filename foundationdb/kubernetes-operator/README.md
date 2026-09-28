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
make failover # freeze one storage server until FDB heals around it, resume it, `make roles`
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

`make failover` freezes the `fdbserver` of one storage pod with `SIGSTOP` (`kubectl exec ...
pkill -STOP -x fdbserver`). Deleting the pod is not enough on kind: the operator recreates it
within seconds, before FDB gives up on the storage server, so nothing degrades. A frozen
process is a hung server: the pod stays Running and the liveness probe (on the sidecar)
passes, so Kubernetes and the operator replace nothing, and FDB has to detect the failure and
re-replicate the data on its own. The victim is the storage server that has applied the most
mutations (one holding data: with this little data there is a single shard on 2 of the 3
servers, and freezing the third would not degrade anything).

The target polls `status json` every 2 s and prints each change of data state, `healthy`,
fault tolerance (`max_zone_failures_without_losing_data`), the number of processes holding a
`storage` role, and whether `status` reports `unreachable_processes`. Once FDB is healthy with
fault tolerance 1 *without* the frozen server, the process is resumed (`SIGCONT`, also on
Ctrl-C or error) and the target waits until all storage servers are back, then for the
`FoundationDBCluster`'s `.status.health.available` / `.fullReplication`, and ends with
`make roles`. Bounded: it resumes after `FREEZE_MAX_POLLS` (150) polls even if nothing
degraded, and gives up after 300 polls. A run on kind:

```
-- froze fdbserver in pod/test-cluster-storage-47656 (SIGSTOP; 3 storage pods)
SECONDS  PHASE    DATA_STATE  HEALTHY  FAULT_TOLERANCE  STORAGE_SERVERS  UNREACHABLE
4        frozen   healthy     true     1                3                true
22       frozen   healthy     true     1                2                false
87       frozen   healing     false    0                2                false
95       frozen   healthy     true     1                2                false
-- FDB re-replicated around the frozen server; resuming it (SIGCONT)
97       resumed  healthy     true     1                2                false
113      resumed  healthy     true     1                3                false
```

- ~4 s: the cluster controller cannot reach the process (`unreachable_processes`).
- ~20 s: the failure monitor drops it; 2 storage servers are left. Fault tolerance still
  reads 1: it follows data distribution's view, and DD does not treat the server as failed yet.
- ~85 s: DD gives up on the server (about a minute after the failure), its shards have one
  replica left, fault tolerance drops to 0 and the data state is `healing` while DD copies
  them to the remaining storage server.
- ~95 s: fully replicated again on the other two servers: healthy, fault tolerance 1.
- After `SIGCONT` the old process rejoins; its storage server was removed, so it comes back
  as a new, empty storage server (new ID) and the count returns to 3.

The kind node's disk is the Docker VM's disk. FDB throttles writes once less than 5% of it is free,
so `cluster.yaml` lowers that floor with `knob_min_available_space_ratio=0.01` (and 256 MiB absolute).
