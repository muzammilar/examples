# YDB — cluster on kind with the YDB Kubernetes Operator

[YDB Kubernetes Operator](https://github.com/ydb-platform/ydb-kubernetes-operator) `0.8.0`
(Helm) managing a `mirror-3-dc` [`Storage`](storage.yaml) of three nodes (one per kind
worker, each worker in its own zone, three in-memory SectorMap disks per node) and a
[`Database`](database.yaml) `/Root/database` served by three dynamic nodes.
[`StorageMonitoring`/`DatabaseMonitoring`](monitoring.yaml) create ServiceMonitors that
the kube-prometheus-stack Prometheus scrapes; Grafana comes with the same chart.

Requires `kind`, `kubectl`, `helm`.

```bash
make up       # kind, kube-prometheus-stack, ydb-operator, Storage + Database, monitoring
make test     # run sql/*.sql against /Root/database via database-0: create, upsert, select, delete
make status   # database endpoints (discovery list), Storage/Database state
make cli      # interactive YQL shell on database-0
make grafana  # print Grafana credentials, port-forward to http://localhost:3000
make down     # delete the kind cluster
```

`up` is split into `kind-cluster`, `prometheus`, `operator`, `cluster`, `monitoring`.

- YDB is pinned to `24.2.7`, the version of the operator's own kind samples. With
  `26.x` the Storage comes up but tenant creation fails with `Group fit error`: operator
  0.8 still generates the old-style static config.
- `storage.yaml` is the operator's `samples/kind/storage-mirror-3dc.yaml` plus the pool
  `geometry` from its e2e test config (`domain_level_end: 256`): with one node per zone
  each disk has to count as a fail domain, otherwise the database never gets a storage
  group (`Group fit error ... no group options`).
- SectorMap disks live in memory: if a storage pod restarts (e.g. OOM-killed on a busy
  Docker VM) before the database is created, the tenant fails with `PDisks# <empty>`.
  Recreate both: `kubectl -n ydb delete database,storage --all && make cluster monitoring`.
- `cr.yandex` (the YDB image registry) is slow and flaky from inside kind. `kind-cluster`
  side-loads the image into every worker with `ctr images import`, from `ydb.tar`
  (`YDB_IMAGE_TAR`) if present, otherwise from local Docker if `docker pull` was done first.
- The chart's webhook cert job still points at the frozen `k8s.gcr.io`; `operator`
  overrides it with `registry.k8s.io/ingress-nginx/kube-webhook-certgen`, and sets
  `metrics.enabled=true`, without which the operator rejects the monitoring objects.
- The image ships YDB CLI 2.8: `yql` instead of `sql`, and `ydb` without a subcommand is
  the interactive shell.
- kube-prometheus-stack is installed without `--wait` (only its CRDs are needed before
  the operator); on a slow Docker Hub Prometheus/Grafana pods arrive a few minutes later.
