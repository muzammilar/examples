# Aerospike Community Edition — 3-node cluster on kind

A plain `StatefulSet` ([`aerospike.yaml`](aerospike.yaml)) — the Aerospike Kubernetes Operator
is aimed at Enterprise Edition — with a headless `Service` whose per-pod DNS names are the
mesh heartbeat seeds. Namespace `test`, RF=2, in memory. Each pod runs the Prometheus
exporter as a sidecar on `:9145`.

Requires `kind`, `kubectl`.

```bash
make up       # kind cluster + StatefulSet, wait until the cluster is stable at size 3
make test     # run aql/test.aql from a throwaway aerospike-tools pod
make status   # asadm info
make cli      # interactive asadm
make metrics  # port-forward aerospike-0's exporter to localhost:9145
make down     # delete the kind cluster
```
