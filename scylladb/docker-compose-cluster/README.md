# ScyllaDB — 3-node cluster with Docker Compose

Three ScyllaDB nodes (1 shard, 750 MiB each, developer mode) plus Prometheus and
Grafana scraping ScyllaDB's built-in metrics endpoint (`:9180/metrics`).

```bash
make up       # start; nodes join one after another (~1–2 min)
make test     # run cql/test.cql: RF=3 keyspace, QUORUM insert, select, delete
make failover # stop scylla-3: QUORUM + LWT still work, CONSISTENCY ALL fails; restart it
make status   # nodetool status — three UN nodes
make cli      # interactive cqlsh on scylla-1
make down     # remove containers and volumes
```

`make failover` stops `scylla-3`, runs [`failover/*.cql`](failover) on `scylla-1` while it is down
(QUORUM and lightweight transactions only need 2 of 3 replicas; `CONSISTENCY ALL` fails with
`Unavailable`), then starts it again and waits until all three nodes are `UN`.

- CQL: `localhost:9042` (scylla-1)
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (anonymous admin) → **ScyllaDB** dashboard
- `make up` also fetches the prebuilt 2026.1 Overview, Detailed, CQL and OS dashboards and the
  latency recording rules from [scylla-monitoring@4.16.1](https://github.com/scylladb/scylla-monitoring/tree/4.16.1/grafana/build/ver_2026.1)
  into the gitignored `grafana/provisioning/dashboards/upstream/` (Grafana folder **upstream**) and
  `prometheus/upstream/`. [`prometheus.yml`](prometheus/prometheus.yml) adds the `cluster`/`dc` labels
  they select on. The OS dashboard needs node_exporter, which this example does not run.

A one-shot privileged `sysctl` container raises `fs.aio-max-nr` first; Docker Desktop's
default (65536) only fits two nodes. It applies to the whole Docker VM until Docker restarts.
All nodes sit in one rack, so the RF=3 keyspace triggers an "RF-rack-valid" warning.
