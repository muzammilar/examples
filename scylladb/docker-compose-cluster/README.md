# ScyllaDB — 3-node cluster with Docker Compose

Three ScyllaDB nodes (1 shard, 750 MiB each, developer mode) plus Prometheus and
Grafana scraping ScyllaDB's built-in metrics endpoint (`:9180/metrics`).

```bash
make up       # start; nodes join one after another (~1–2 min)
make test     # run cql/test.cql: RF=3 keyspace, QUORUM insert, select, delete
make status   # nodetool status — three UN nodes
make cli      # interactive cqlsh on scylla-1
make down     # remove containers and volumes
```

- CQL: `localhost:9042` (scylla-1)
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (anonymous admin) → **ScyllaDB** dashboard

A one-shot privileged `sysctl` container raises `fs.aio-max-nr` first; Docker Desktop's
default (65536) only fits two nodes. It applies to the whole Docker VM until Docker restarts.
All nodes sit in one rack, so the RF=3 keyspace triggers an "RF-rack-valid" warning.
For the full dashboard set see [scylla-monitoring](https://github.com/scylladb/scylla-monitoring).
