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
make status   # FoundationDBCluster + fdbcli `status details`
make cli      # interactive fdbcli
make metrics  # port-forward the exporter to localhost:9444
make down     # delete the kind cluster
```

`up` is split into `kind-cluster`, `operator`, `cluster`, `exporter` if you want to go step by step.
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

The kind node's disk is the Docker VM's disk. FDB throttles writes once less than 5% of it is free,
so `cluster.yaml` lowers that floor with `knob_min_available_space_ratio=0.01` (and 256 MiB absolute).
