# ScyllaDB — 3-node cluster on kind with ScyllaDB Operator

[ScyllaDB Operator](https://operator.docs.scylladb.com/) `v1.22.0` (Helm) managing a
3-member `ScyllaCluster` in developer mode ([`scyllacluster.yaml`](scyllacluster.yaml)),
monitored by a `ScyllaDBMonitoring` (Grafana with the scylla-monitoring dashboards) backed
by a prometheus-operator managed `Prometheus`.

Requires `kind`, `kubectl`, `helm`.

```bash
make up       # kind, cert-manager, prometheus-operator, scylla-operator, ScyllaCluster, monitoring
make test     # run cql/test.cql: RF=3 keyspace, QUORUM insert, select, delete
make status   # nodetool status
make cli      # interactive cqlsh
make grafana  # print Grafana credentials, port-forward to https://localhost:3000
make down     # delete the kind cluster
```

`up` is split into `kind-cluster`, `operator`, `cluster`, `monitoring`.
[`sysctl-daemonset.yaml`](sysctl-daemonset.yaml) raises `fs.aio-max-nr` on the kind node,
which on Docker Desktop applies to the whole Docker VM until Docker restarts.
