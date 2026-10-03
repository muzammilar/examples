# OceanBase

Website: https://en.oceanbase.com/

The examples use the MySQL-mode user tenant `test` (port 2881); cluster-wide views are
read from the `sys` tenant.

- [`single-node/`](single-node) — one observer from the `oceanbase/oceanbase-ce` image (`MODE=mini`) on Docker Compose.

- [`docker-compose-cluster/`](docker-compose-cluster) — three observers in three zones (zone1–3) on Docker Compose,
  bootstrapped by hand without obd; tenant `test` has locality `F@zone1, F@zone2, F@zone3`, with a
  `make failover` that kills the leader's observer.

- [`kubernetes-operator/`](kubernetes-operator) — ob-operator 2.3.4 (Helm) on kind: an `OBCluster`
  with one observer (standalone mode) and an `OBTenant` `test`. The operator's resource floors
  are lowered through its `OB_OPERATOR_RESOURCE_MIN*` environment variables.

The cluster needs about 20 GB of Docker memory (3 × 6G `memory_limit` plus overhead) and ~20 GB of
disk. OceanBase's own recommendations for clusters are `obd` or
[ob-operator](https://github.com/oceanbase/ob-operator). By default ob-operator 2.3.4 refuses
observers below 8Gi memory, 30Gi data, 30Gi redo log and 10Gi log storage. Those floors are
operator config and can be lowered (see [`kubernetes-operator/`](kubernetes-operator/README.md#resource-floors)),
but data and redo storage must still be at least 3 × `memory_limit`, and 95% of the redo size is
preallocated as log disk.

## Benchmark

sysbench on one `mini` observer, 4 CPUs / 8 GB (memory raised from the usual 6 GB, because the observer's `memory_limit` floor is 6G; Apple M4 Pro, Docker VM aarch64, 2026-09-28): point selects 82k/s at p95 0.6 ms with 32 threads, `oltp_read_only` 4.6k tps (74k qps). `oltp_read_write` peaks at ~960 tps with 8 threads and halves at 32. Reads scale on the capped CPUs, while writes saturate early. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).

Cluster (3 observers, 2 CPUs / 7 GB each, all leaders in zone1, sysbench at 32 threads): point selects
40k/s, `oltp_read_write` ~900 tps. Killing the leader's observer moved leadership to zone2 and the
next write committed ~4.6 s after the kill. Full tables: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).

ob-operator on kind (`kubernetes-operator/`, one observer at `memory_limit` 4G, 12Gi data and redo
log PVCs): from an empty kind cluster to a `running` OBCluster in about 4 minutes (cert-manager
and the image side-load 73 s, operator 52 s, OBCluster 131 s), and the OBTenant took 29 s more. The kind
worker used 3.7 GiB; the redo log PVC held 12 GB of preallocated log disk. A 3-zone cluster was
not run: it would need about 40 GB of preallocated disk. Details:
[`kubernetes-operator/README.md`](kubernetes-operator/README.md).

## Known issues

- ob-operator 2.3.4 rejects small observers (`The minimum memory size of OBCluster is 8Gi`, `...
  data storage size ... is 30Gi`, `... redo log storage size ... is 30Gi`, `... log storage size ...
  is 10Gi`). Lower the floors with `OB_OPERATOR_RESOURCE_MINMEMORYSIZE` / `MINDATADISKSIZE` /
  `MINREDOLOGDISKSIZE` / `MINLOGDISKSIZE` on the manager deployment. The message keeps printing
  the built-in values.
- `kind load docker-image` fails on the multi-arch `oceanbase-cloud-native` image (`ctr: content
  digest ...: not found`). Save one platform and import it with `ctr` instead.
