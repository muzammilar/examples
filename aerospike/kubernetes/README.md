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

## Why not the Aerospike Kubernetes Operator

AKO's [documentation](https://aerospike.com/docs/kubernetes/install/limitations/) says *"Community Edition is not supported. AKO only supports Enterprise and Federal editions"*,
and its validating webhook enforces it: [`validateImage`](https://github.com/aerospike/aerospike-kubernetes-operator/blob/v4.5.0/internal/webhook/v1/aerospikecluster_validating_webhook.go#L1721-L1732)
(v4.5.0) rejects any `AerospikeCluster` whose image name contains neither `enterprise` nor `federal`, so `aerospike/aerospike-server` never gets created.
Even with the check sidestepped, AKO quiesces a node before every rolling restart, upgrade or scale-down ([`aero_info_calls.go`](https://github.com/aerospike/aerospike-kubernetes-operator/blob/v4.5.0/internal/controller/cluster/aero_info_calls.go#L94)),
and CE answers `quiesce:` with `ERROR:25:enterprise only`, as it does for racks, access control, TLS and strong consistency.
Config or resource changes could therefore never complete, so this example keeps to a plain StatefulSet.
