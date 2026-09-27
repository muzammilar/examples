# FoundationDB — cluster on kind with the FoundationDB Kubernetes Operator

[fdb-kubernetes-operator](https://github.com/FoundationDB/fdb-kubernetes-operator) `v2.37.0`
managing a `double`-redundancy `FoundationDBCluster` ([`cluster.yaml`](cluster.yaml), adapted
from the operator's local-testing sample) on a one-node kind cluster, plus
[foundationdb-exporter](https://github.com/aikoven/foundationdb-exporter) reading the
cluster file the operator publishes.

Requires `kind`, `kubectl`, `curl`.

```bash
make up       # kind cluster, CRDs + operator, FoundationDBCluster, exporter
make test     # run queries/test.fdbcli on a log pod
make status   # fdbcli status
make cli      # interactive fdbcli
make metrics  # port-forward the exporter to localhost:9444
make down     # delete the kind cluster
```

`up` is split into `kind-cluster`, `operator`, `cluster`, `exporter` if you want to go step by step.
`faultDomain: foundationdb.org/none` lets all processes share the single kind node.
