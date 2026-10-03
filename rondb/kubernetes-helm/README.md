# RonDB — cluster on kind with the official Helm chart

The [RonDB Helm chart](https://github.com/logicalclocks/rondb-helm) (`rondb/rondb` 26.2.20,
RonDB 26.02.11, from `https://logicalclocks.github.io/rondb-helm`) on a one-node kind cluster,
sized from the chart's `values/minikube/mini.yaml` ([`values.yaml`](values.yaml)):

| pod | StatefulSet | role |
|-----|-------------|------|
| `mgmds-0` | `mgmds` | management server (node 65) |
| `node-group-0-0`, `node-group-0-1` | `node-group-0` | data nodes 1 + 2, node group 0, `activeDataReplicas: 2` |
| `mysqlds-0` | `mysqlds` (HPA 1–2) | MySQL Server, Service `mysqld:3306` (node ids 67 + 68: `MySQLdSlotsPerNode: 2`) |
| `rdrs-0` | `rdrs` | REST API server, Services `rdrs` / `rdrs-cluster-ip` |

Requires `kind`, `kubectl`, `helm` (the repo's `flake.nix` dev shell has them).

```bash
make up        # kind cluster, import images into the node, helm install --wait (~45 s once images are in)
make test      # the chart's Helm tests (generate-data, verify-data), then sql/demo.sql
make failover  # sysbench oltp_read_write through Service mysqld; force-delete pod node-group-0-1 30 s in
make node-groups  # helm upgrade --set clusterSize.numNodeGroups=2: refused by the chart (expected)
make status    # pods, ndb_mgm -e show, all report memory
make cli       # mysql as root in mysqlds-0
make down      # delete the kind cluster
```

- Images (all multi-arch, native arm64 on Apple silicon): `hopsworks/rondb:26.02.11` for every
  RonDB pod, `hopsworks/hwutils:1.7` for init/check containers, `python:3.12-slim` for the Helm
  tests. `make images` pulls them on the host and imports them into the kind node. The chart's
  `mysqld_exporter` (from `docker.hops.works`) stays disabled.
- Data nodes: `ndbmtdsMiB: 3000`, so the chart writes `TotalMemoryConfig=2250M` (75% of the
  limit, and RonDB's floor is 2 GB) and `NumCPUs=2`. The kind node used ~6.1 GB in total, and the
  local-path volumes ~4.7 GB (the redo log, undo log and disk-column tablespace are preallocated;
  `redoLogGiB: 2` is the chart's minimum).
- Passwords: Secret `mysql-passwords` (`root`, and `helm` for the chart's cluster user), generated
  at install. TLS, ingress and backups are off. `isMultiNodeCluster: false` drops the pod
  anti-affinity so everything fits on one kind node.
- The chart writes `NoOfReplicas=3` with a third data node slot (`NodeActive=0`), so
  `activeDataReplicas` can go to 3 with `helm upgrade` (not run here). `minNumMySQLServers` /
  `maxNumMySQLServers` bound the MySQL Server HPA.

## Failover

`make failover` loads 4 × 50,000 rows with sysbench 1.0.20 (a pod from [`bench/Dockerfile`](bench/Dockerfile)),
runs `oltp_read_write` with 8 threads for 120 s through Service `mysqld`, and 30 s in force-deletes
data node pod `node-group-0-1` (`--grace-period=0 --force`, no clean shutdown). 2026-10-03, kind on
Docker Desktop, Apple M4 Pro:

```
            >>>   30s kill pod node-group-0-1
            >>>   32s node 2 not connected
   30s  tps    753.4  p99    50.11 ms  err/s   0.00
   35s  tps    747.4  p99    52.89 ms  err/s   1.60
   40s  tps    763.8  p99    53.85 ms  err/s   0.00
   ...
   60s  tps    751.3  p99    48.34 ms  err/s   0.00
            >>>   62s node 2 started
   65s  tps    754.0  p99    49.21 ms  err/s   0.00
```

Node 1 has a replica of every fragment, so the load never stopped: throughput stayed at 730–767 tps
in every 5 s interval. 8 transactions failed when node 2 went away (the transactions it was part
of), out of 90,252. The StatefulSet recreated the pod on its PVC and data node 2 did a node restart
(`Start phase 101 completed (node restart)`, `total elapsed=18s`). It was `started` again 32 s after
the kill.

## Scaling

What the chart scales through values ([chart README](https://github.com/logicalclocks/rondb-helm#helm-charts-capabilities)):
data node replicas per node group (`activeDataReplicas`, 1–3, by activating the prepared slots) and
the number of MySQL Servers / REST servers (HPA bounds). It does **not** change the number of node
groups:

```
$ helm upgrade ... --set clusterSize.numNodeGroups=2
Error: UPGRADE FAILED: execution error at (rondb/templates/topology-immutability.yaml:36:4):

ERROR: clusterSize.numNodeGroups is immutable (deployed=1, requested=2).
RonDB does not support online add/remove of node groups — take a backup, then reinstall with the new topology and restoreFromBackup.backupId set to that backup.
Bypass: --set forceNodeGroupChange=true (DESTRUCTIVE).
```

That is the chart's limit, and the chart's wording. RonDB itself does add node groups online
(`CREATE NODEGROUP` + `ALTER TABLE ... REORGANIZE PARTITION`, shown on Docker Compose in
[`../online-scaling/`](../online-scaling)). What it cannot do is remove a node group that holds
data.

## Benchmark

`make failover` above is the workload: sysbench 1.0.20 `oltp_read_write`, 8 threads, 4 × 50,000 rows,
one MySQL Server (2 CPU limit) and two data nodes (2 CPU limit each) on one kind node: **752 tps**
(15,000 qps) at p99 51 ms over 120 s, including the data node kill. The chart also ships a benchmark
Job (`benchmarking.enabled: true`, RonDB's `bench_run.sh` with its sysbench 0.4.12 fork). It is not
run here.

## Known issues

- `kind load docker-image` fails for these images on Docker Desktop (containerd image store):
  `ctr: content digest sha256:709c8b69...: not found`. `make images` uses `docker save --platform
  linux/<arch> | ctr -n k8s.io images import` instead.
- `resources.requests.storage.redoLogGiB: 1` is rejected: `values don't meet the specifications of
  the schema(s) ... at '/resources/requests/storage/redoLogGiB': minimum: got 1, want 2`.
- The chart's Helm test pods (`generate-data`, `verify-data`) `pip install` PyMySQL and
  cryptography from PyPI when they run, so `make test` needs internet access from the cluster.
