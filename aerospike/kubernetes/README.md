# Aerospike Community Edition — 3-node cluster on kind

Three Aerospike pods on a single-node kind cluster: a plain `StatefulSet`
([`aerospike.yaml`](aerospike.yaml)) — the Aerospike Kubernetes Operator is aimed at Enterprise
Edition — with a headless `Service` whose per-pod DNS names are the mesh heartbeat seeds.
Namespace `test`, RF=2, in memory. Each pod runs the Prometheus exporter as a sidecar on `:9145`.

Requires `kind`, `kubectl`.

```bash
make up       # kind cluster + StatefulSet, wait until the cluster is stable at size 3
              # (= make kind-cluster, then make cluster to (re)apply aerospike.yaml)
make test     # run aql/test.aql from a throwaway aerospike-tools pod
make failover # delete pod aerospike-2: size 2, all records readable; wait for its replacement
make status   # asadm info
make cli      # interactive asadm
make metrics  # port-forward aerospike-0's exporter to localhost:9145
make down     # delete the kind cluster
```

The first `make test` (and `make failover`) pulls `aerospike/aerospike-tools` inside kind, which
takes a few minutes; later runs start the pod in seconds.

`make failover` writes 10 records ([`failover/*.aql`](failover)) from an aerospike-tools pod,
deletes pod `aerospike-2` and waits for `cluster-stable:size=2`: with RF=2 every partition still has
a copy on `aerospike-0` or `aerospike-1`, so all 10 records read back. The StatefulSet then recreates
`aerospike-2` under the same name; the target waits for the rollout and for
`cluster-stable:size=3;ignore-migrations=false` (migrations finished) and reads again.
