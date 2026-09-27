# FoundationDB — 3-node cluster with Docker Compose

Three `fdbserver` containers in `double` redundancy, all three coordinators, plus
[foundationdb-exporter](https://github.com/aikoven/foundationdb-exporter), Prometheus and Grafana.

```bash
make up       # start everything and `configure new double ssd`
make test     # run queries/test.fdbcli: set, get, getrange, clear
make status   # fdbcli status (3 machines, 3 coordinators, fault tolerance 1)
make cli      # interactive fdbcli
make down     # remove containers and volumes
```

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (anonymous admin) → **FoundationDB** dashboard
- `make up` also fetches the exporter's own dashboard from
  [aikoven/foundationdb-exporter@b8e124d](https://github.com/aikoven/foundationdb-exporter/tree/b8e124de1fd83b6467bafedb6ad438e48eaaed31/grafana)
  into the gitignored `grafana/provisioning/dashboards/upstream/` → Grafana folder **upstream**.
  It filters on a `namespace` label, which [`prometheus.yml`](prometheus/prometheus.yml) sets.

The cluster file (set via `FDB_CLUSTER_FILE_CONTENTS` and mounted into the exporter as
[`fdb.cluster`](fdb.cluster)) names the coordinators by hostname, supported since FDB 7.1.
The exporter image is amd64-only and runs under emulation on Apple silicon.
