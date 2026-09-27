# Aerospike Community Edition — 3-node cluster with Docker Compose

Three CE nodes joined over mesh heartbeats ([`aerospike.conf`](aerospike.conf)),
namespace `test` with `replication-factor 2` in memory. Each node has an
[aerospike-prometheus-exporter](https://github.com/aerospike/aerospike-prometheus-exporter)
next to it; Prometheus scrapes all three and Grafana shows a small dashboard.

```bash
make up       # start and wait until the cluster is stable at size 3
make test     # run aql/test.aql (via aerospike-tools): insert, select, delete, SHOW SETS
make status   # asadm info
make cli      # interactive asadm
make down     # remove containers
```

- Client: `localhost:3000` (aerospike-1)
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3001 (anonymous admin) → **Aerospike** dashboard
- `make up` also fetches the Cluster, Node, Namespace, Set and Latency dashboards from
  [aerospike-monitoring@v3.20.0](https://github.com/aerospike/aerospike-monitoring/tree/v3.20.0/config/grafana/dashboards)
  into the gitignored `grafana/provisioning/dashboards/upstream/` → Grafana folder **upstream**.
  Panels for device/pmem storage, strong consistency and alerts stay empty in this setup.

`SHOW SETS` in the test output lists the remaining record on two of the three nodes (RF=2).
The OS-tuning warnings in the server log (THP, swappiness, min-free-kbytes) are expected
in containers.
