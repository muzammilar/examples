# RonDB — cluster on kind with the official Helm chart

The [RonDB Helm chart](https://github.com/logicalclocks/rondb-helm) (`rondb/rondb` 26.2.20,
RonDB 26.02.11, from `https://logicalclocks.github.io/rondb-helm`) on a one-node kind cluster,
sized from the chart's `values/minikube/mini.yaml` ([`values.yaml`](values.yaml)).
Requires `kind`, `kubectl`, `helm` (in the repo's `flake.nix` dev shell).

## Quick start

```bash
make up           # kind cluster, import images into the node, helm install --wait (~45 s once images are in)
make test         # the chart's Helm tests (generate-data, verify-data), then sql/demo.sql
make failover     # sysbench oltp_read_write through Service mysqld; force-delete pod node-group-0-1 30 s in
make node-groups  # helm upgrade --set clusterSize.numNodeGroups=2: refused by the chart (expected)
make status       # pods, ndb_mgm -e show, all report memory
make cli          # mysql as root in mysqlds-0
make down         # delete the kind cluster
```

## Setup

| pod | StatefulSet | role |
|-----|-------------|------|
| `mgmds-0` | `mgmds` | management server (node 65) |
| `node-group-0-0`, `node-group-0-1` | `node-group-0` | data nodes 1 + 2, node group 0, `activeDataReplicas: 2` |
| `mysqlds-0` | `mysqlds` (HPA 1–2) | MySQL Server, Service `mysqld:3306` (node ids 67 + 68: `MySQLdSlotsPerNode: 2`) |
| `rdrs-0` | `rdrs` | REST API server, Services `rdrs` / `rdrs-cluster-ip` |

| item | value |
|---|---|
| Images (multi-arch, native arm64 on Apple silicon) | `hopsworks/rondb:26.02.11` (every RonDB pod), `hopsworks/hwutils:1.7` (init/check containers), `python:3.12-slim` (Helm tests). `make images` pulls them on the host and imports them into the kind node. The chart's `mysqld_exporter` (from `docker.hops.works`) stays disabled |
| Data nodes | `ndbmtdsMiB: 3000` → `TotalMemoryConfig=2250M` (75% of the limit; RonDB's floor is 2 GB), `NumCPUs=2` |
| Footprint | kind node ~6.1 GB; local-path volumes ~4.7 GB (redo log, undo log and disk-column tablespace are preallocated; `redoLogGiB: 2` is the chart's minimum) |
| Passwords | Secret `mysql-passwords` (`root`, and `helm` for the chart's cluster user), generated at install |
| Off | TLS, ingress, backups. `isMultiNodeCluster: false` drops pod anti-affinity so everything fits on one kind node |
| Replicas | chart writes `NoOfReplicas=3` with a third data node slot (`NodeActive=0`), so `activeDataReplicas` can go to 3 with `helm upgrade` (not run here); `minNumMySQLServers` / `maxNumMySQLServers` bound the MySQL Server HPA |

## Failover

`make failover` loads 4 × 50,000 rows with sysbench 1.0.20 (a pod from [`bench/Dockerfile`](bench/Dockerfile)),
runs `oltp_read_write` with 8 threads for 120 s through Service `mysqld`, and at 30 s force-deletes
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

- Node 1 has a replica of every fragment, so load never stopped: 730–767 tps in every 5 s interval.
- 8 of 90,252 transactions failed when node 2 went away (those it was part of).
- The StatefulSet recreated the pod on its PVC; data node 2 did a node restart
  (`Start phase 101 completed (node restart)`, `total elapsed=18s`) and was `started` 32 s after the kill.

## Scaling

Through values the chart scales data node replicas per node group (`activeDataReplicas`, 1–3, by
activating prepared slots) and the number of MySQL / REST servers (HPA bounds)
([chart README](https://github.com/logicalclocks/rondb-helm#helm-charts-capabilities)). It does
**not** change the number of node groups:

```
$ helm upgrade ... --set clusterSize.numNodeGroups=2
Error: UPGRADE FAILED: execution error at (rondb/templates/topology-immutability.yaml:36:4):

ERROR: clusterSize.numNodeGroups is immutable (deployed=1, requested=2).
RonDB does not support online add/remove of node groups — take a backup, then reinstall with the new topology and restoreFromBackup.backupId set to that backup.
Bypass: --set forceNodeGroupChange=true (DESTRUCTIVE).
```

That is the chart's limit and wording. RonDB itself adds node groups online (`CREATE NODEGROUP` +
`ALTER TABLE ... REORGANIZE PARTITION`, see [`../online-scaling/`](../online-scaling)); it cannot
remove a node group that holds data.

## Benchmark

The `make failover` workload above: sysbench 1.0.20 `oltp_read_write`, 8 threads, 4 × 50,000 rows,
one MySQL Server (2 CPU limit), two data nodes (2 CPU limit each), one kind node: **752 tps**
(15,000 qps), p99 51 ms over 120 s, including the data node kill. The chart's own benchmark Job
(`benchmarking.enabled: true`, RonDB's `bench_run.sh` with its sysbench 0.4.12 fork) is not run.

## Known issues

- `kind load docker-image` fails for these images on Docker Desktop (containerd image store):
  `ctr: content digest sha256:709c8b69...: not found`. `make images` uses `docker save --platform
  linux/<arch> | ctr -n k8s.io images import` instead.
- `resources.requests.storage.redoLogGiB: 1` is rejected: `values don't meet the specifications of
  the schema(s) ... at '/resources/requests/storage/redoLogGiB': minimum: got 1, want 2`.
- The chart's Helm test pods (`generate-data`, `verify-data`) `pip install` PyMySQL and
  cryptography from PyPI at run time, so `make test` needs internet access from the cluster.
