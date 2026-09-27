# YDB — 3 storage + 2 database nodes with Docker Compose

A minimal fault-tolerant YDB cluster using [configuration V2](https://ydb.tech/docs/en/devops/deployment-options/manual/initial-deployment/deployment-configuration-v2)
in insecure mode (no TLS, no auth):

- `ydb-storage-{1,2,3}` — static nodes, one per "data center", three sparse
  16 GiB file disks each: the smallest `mirror-3-dc` layout ([`config.yaml`](config.yaml)).
- `ydb-dynamic-{1,2}` — dynamic nodes serving database `/Root/testdb`.
- Prometheus scraping each node's `/counters/counters=<group>/prometheus`, and Grafana.

```bash
make up       # start, `ydb admin cluster bootstrap`, create /Root/testdb, wait for SQL
make test     # run sql/*.sql: create table, upsert, select, delete
make status   # cluster health from the viewer API
make cli      # interactive YQL shell against /Root/testdb
make down     # remove containers (disks live inside them)
```

- gRPC: `grpc://localhost:2136/Root/testdb` (ydb-dynamic-1)
- Embedded UI: http://localhost:8765 (ydb-storage-1)
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (anonymous admin) → **YDB** dashboard
- `make up` also fetches the CPU, DB overview, DB status, Actors, gRPC, Query engine, TxProxy and
  DataShard dashboards from [ydb-platform/ydb@26.2.1.14](https://github.com/ydb-platform/ydb/tree/26.2.1.14/ydb/deploy/helm/ydb-prometheus/dashboards)
  into the gitignored `grafana/provisioning/dashboards/upstream/` → Grafana folder **upstream**.
  They expect the Helm chart's naming, so [`prometheus.yml`](prometheus/prometheus.yml) prefixes
  metric names with their counter group (`utils_`, `kqp_`, …) and sets `container`. Per-pool panels
  (User/System/Batch/IC) stay empty: with `use_auto_config` on 2 CPUs ydbd runs only Common and IO.

`ydbd` is taken from the `local-ydb` image (amd64-only, emulated on Apple silicon).
