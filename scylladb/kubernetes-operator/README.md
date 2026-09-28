# ScyllaDB — 3-node cluster on kind with ScyllaDB Operator

[ScyllaDB Operator](https://operator.docs.scylladb.com/) `v1.22.0` (Helm) managing a
3-member `ScyllaCluster` in developer mode ([`scyllacluster.yaml`](scyllacluster.yaml)),
monitored by a `ScyllaDBMonitoring` (Grafana with the scylla-monitoring dashboards) backed
by a prometheus-operator managed `Prometheus`.

Requires `kind`, `kubectl`, `helm`.

```bash
make up       # kind, cert-manager, prometheus-operator, scylla-operator, ScyllaCluster, monitoring
make test     # run cql/test.cql: RF=3 keyspace, QUORUM insert, select, delete
make failover # delete pod scylla-dc1-rack1-2: QUORUM + LWT still work, ALL fails; wait until UN again
make status   # nodetool status
make cli      # interactive cqlsh
make grafana  # print Grafana credentials, port-forward to https://localhost:3000
make down     # delete the kind cluster
```

`up` is split into `kind-cluster`, `operator`, `cluster`, `monitoring`. The operator chart runs
with one operator and one webhook replica (`--set replicas=1 --set webhookServerReplicas=1`)
to fit a one-node kind cluster.
[`sysctl-daemonset.yaml`](sysctl-daemonset.yaml) raises `fs.aio-max-nr` on the kind node,
which on Docker Desktop applies to the whole Docker VM until Docker restarts.

`make test` prints a warning for the RF=3 keyspace: `Keyspace 'demo' is not RF-rack-valid: the
replication factor doesn't match the rack count in at least one datacenter. A rack failure may
reduce availability.` All three members sit in one rack (`rack1`), so RF=3 != 1 rack; ScyllaDB
recommends RF = number of racks so each rack holds one replica. Harmless on a one-node kind cluster.

## Failover

`make failover` deletes pod `scylla-dc1-rack1-2`, waits until `scylla-dc1-rack1-0` sees it `DN`,
then runs the [`failover/`](failover) CQL: a `QUORUM` write + read succeeds (2 of 3 replicas),
`CONSISTENCY ALL` fails with `Unavailable ... Requires 3, alive 2`, and lightweight transactions
(Paxos, which needs a quorum) still apply. The StatefulSet recreates the pod on its PVC; the target
waits (bounded, 180 x 5 s) until it is Ready and all three nodes are `UN`, reruns the `ALL` read,
and waits for the `ScyllaCluster` to be `Available`. About a minute on kind.

The Grafana from `ScyllaDBMonitoring` ships the scylla-monitoring dashboards for many ScyllaDB
versions (a set each for 2024.1 through 2026.2, `master`, plus Scylla Manager 3); open the
`scylladb-2026.1` ones to match this cluster.
