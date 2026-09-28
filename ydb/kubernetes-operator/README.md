# YDB — cluster on kind with the YDB Kubernetes Operator

[YDB Kubernetes Operator](https://github.com/ydb-platform/ydb-kubernetes-operator) `0.8.0`
(Helm) managing a `mirror-3-dc` [`Storage`](storage.yaml) of three nodes (one per kind
worker, each worker in its own zone, three in-memory SectorMap disks per node) and a
[`Database`](database.yaml) `/Root/database` served by three dynamic nodes.
[`StorageMonitoring`/`DatabaseMonitoring`](monitoring.yaml) create ServiceMonitors that
the kube-prometheus-stack Prometheus scrapes; Grafana comes with the same chart.

Requires `kind`, `kubectl`, `helm`. The whole setup (control plane, three workers,
three storage and three database pods with in-memory disks, kube-prometheus-stack)
needs about 11.5 GiB of memory in the Docker VM with YDB 26.2 (9.4 GiB with 24.2).

```bash
make up       # kind, kube-prometheus-stack, ydb-operator, Storage + Database, monitoring
make test     # run sql/*.sql against /Root/database via database-0: create, upsert, select, delete
make status   # database endpoints (discovery list), Storage/Database state
make cli      # interactive YQL shell on database-0
make grafana  # wait for Grafana, print its credentials, port-forward to http://localhost:3000
make down     # delete the kind cluster
```

`up` is split into `kind-cluster`, `prometheus`, `operator`, `cluster`, `monitoring`.

- YDB is pinned to `26.2.1.14` (tested on a fresh cluster: Storage and Database Ready in
  about 1.5 min once the image is on the workers; `24.2.7`, the version of the operator's own
  kind samples, works the same way). An earlier `Group fit error` with 26.x was not a
  version problem but the missing pool `geometry` below.
- `storage.yaml` is the operator's `samples/kind/storage-mirror-3dc.yaml` plus the pool
  `geometry` from its e2e test config (`domain_level_end: 256`): with one node per zone
  each disk has to count as a fail domain, otherwise the database never gets a storage
  group (`Group fit error ... no group options`), with 24.x and 26.x alike.
- SectorMap disks live in memory: if a storage pod restarts (e.g. OOM-killed on a busy
  Docker VM) before the database is created, the tenant fails with `PDisks# <empty>`.
  Recreate both: `kubectl -n ydb delete database,storage --all && make cluster monitoring`.
- The YDB image is linux/amd64 only; on Apple silicon every YDB pod runs emulated, which
  is why start-up is slow.
- `cr.yandex` (the YDB image registry) is slow and flaky from inside kind: a plain
  `make up` that lets each worker pull the image can take hours. `kind-cluster`
  side-loads the image into every worker with `ctr images import --digests`, from
  `ydb.tar` (`YDB_IMAGE_TAR`, gitignored) if present, otherwise from local Docker if
  `docker pull` was done first. To make the tar once:
  `docker pull --platform linux/amd64 cr.yandex/crptqonuodf51kdj7a7d/ydb:26.2.1.14 &&
  docker save --platform linux/amd64 -o ydb.tar cr.yandex/crptqonuodf51kdj7a7d/ydb:26.2.1.14`
  (~480 MB; a host pull may need a few retries on `TLS handshake timeout`). The operator
  image `cr.yandex/yc/ydb-kubernetes-operator:0.8.0` (37 MB) comes from the same registry and
  is side-loaded as well when it is in local Docker
  (`docker pull --platform linux/amd64 cr.yandex/yc/ydb-kubernetes-operator:0.8.0`).
  Measured on a fresh cluster without either: the operator image took 9 min 47 s to pull,
  and after the 15 min `make cluster` wait only two of the three workers had the YDB image
  (13 min 19 s for the first), so the target timed out. With both side-loaded, `make up`
  takes about 6 min (the kube-prometheus-stack and webhook cert images are still pulled).
- The chart's webhook cert job still points at the frozen `k8s.gcr.io`; `operator`
  overrides it with `registry.k8s.io/ingress-nginx/kube-webhook-certgen`, and sets
  `metrics.enabled=true`, without which the operator rejects the monitoring objects.
- The image ships YDB CLI 2.31: `make test` uses `ydb sql -f -` (one file per query, since
  YDB rejects DDL and DML in one query), and `ydb` without a subcommand is the interactive
  shell (`YQL>`, Ctrl+D or `exit` to leave). The CLI's release check needs curl, which the
  image lacks, so `test`/`status`/`cli` first run `ydb version --disable-checks` to silence
  its warning. Re-running `make test` prints `path exist, request accepts it` for the
  `CREATE TABLE IF NOT EXISTS`; that is a notice, not a failure.
- kube-prometheus-stack is installed without `--wait` (only its CRDs are needed before
  the operator); on a slow Docker Hub Prometheus/Grafana pods arrive a few minutes later,
  and `make grafana` waits for the Grafana rollout before port-forwarding.
- No YDB dashboards are provisioned: Grafana has only kube-prometheus-stack's
  Kubernetes dashboards. The YDB metrics are in Prometheus (Explore, or import YDB's
  dashboards from `ydb/deploy/helm/ydb-prometheus/dashboards` in the YDB repo).
