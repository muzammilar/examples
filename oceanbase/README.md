# OceanBase

Website: https://en.oceanbase.com/ · GitHub: https://github.com/oceanbase/oceanbase

All examples use the MySQL-mode user tenant `test` (port 2881 unless noted); cluster-wide views are
read from the `sys` tenant. OceanBase CE 4.4.2.1.

| Folder | What |
|---|---|
| [`single-node/`](single-node) | One observer from `oceanbase/oceanbase-ce` (`MODE=mini`) on Docker Compose; SQL tests and sysbench |
| [`docker-compose-cluster/`](docker-compose-cluster) | Three observers in zone1–3, bootstrapped by hand without obd; tenant `test` with locality `F@zone1, F@zone2, F@zone3`; `make failover` kills the leader's observer |
| [`htap-orders/`](htap-orders) | Go program on one `mini` observer (port 2891): OLTP and analytics at the same time on one hybrid row/column table (`WITH COLUMN GROUP(all columns, each column)`); row vs column scans, alone and under writes; freshness of column-store reads |
| [`scale-out-in/`](scale-out-in) | The cluster grown 3 → 6 observers and back (`ALTER SYSTEM ADD/DELETE SERVER`, `ALTER RESOURCE TENANT test UNIT_NUM = 2/1`) with obproxy and sysbench running; in-place `ALTER RESOURCE UNIT`. Observers at `memory_limit` 4G so six fit in a 24 GB Docker VM |
| [`kubernetes-operator/`](kubernetes-operator) | ob-operator 2.3.4 (Helm) on kind: one-observer `OBCluster` (standalone mode) and `OBTenant` `test`; resource floors lowered via `OB_OPERATOR_RESOURCE_MIN*` |

## Cluster resources

- The 3-observer cluster needs ~20 GB of Docker memory (3 × 6G `memory_limit` plus overhead) and ~20 GB of disk.
- OceanBase recommends `obd` or [ob-operator](https://github.com/oceanbase/ob-operator) for clusters.
  ob-operator 2.3.4 by default refuses observers below 8Gi memory, 30Gi data, 30Gi redo log and 10Gi
  log storage. The floors can be lowered ([`kubernetes-operator/`](kubernetes-operator/README.md#resource-floors)),
  but data and redo storage must still be ≥ 3 × `memory_limit`, and 95% of the redo size is
  preallocated as log disk.

## Benchmark summary

Apple M4 Pro, Docker VM aarch64. Full method and tables in each example.

| Example | Setup | Result |
|---|---|---|
| [single-node](single-node/README.md#benchmark) (2026-09-28) | sysbench, one `mini` observer capped at 4 CPUs / 8 GB (8 GB, not the usual 6 GB, because the observer's `memory_limit` floor is 6G) | point select 82k/s, p95 0.6 ms (32 threads); `oltp_read_only` 4.6k tps (74k qps); `oltp_read_write` peaks ~960 tps at 8 threads, halves at 32. Reads scale on the capped CPUs, writes saturate early |
| [docker-compose-cluster](docker-compose-cluster/README.md#benchmark) | sysbench, 3 observers at 2 CPUs / 7 GB, all leaders in zone1, 32 threads | point select 40k/s; `oltp_read_write` ~900 tps. Killing the leader's observer moved leadership to zone2; next write committed ~4.6 s after the kill |
| [htap-orders](htap-orders/README.md#sample-output) (2026-10-02) | one `mini` observer, 6 CPUs, 2M orders, hybrid row/column table | aggregations 5–170x faster via column store. With 16 OLTP workers, 2 column-store analytics workers finished 3.6x more queries than row-store ones; OLTP kept 74% of its solo ~1.5k tps (65% with row-store scans). 2026-10-03 rerun: 37% vs 29%, same ordering |
| [scale-out-in](scale-out-in/README.md) | 16 sysbench threads via obproxy, 1-CPU / 1.5G units | scale-out split the user log stream, transferred 8 of 20 partitions in 2 s (balance job 30 s); scale-in merged back, 84 s total. Both online, no failed statements, one 5–10 s dip each. At 6 servers: point select 54k → 57k qps via obproxy, 49k → 26k sent to ob1 only (it forwards half); `oltp_read_write` 651 → 366 tps (two-phase commit across log streams). Unit CPU 1 → 3: no change (no cgroup). 1G unit stalled the tenant |
| [kubernetes-operator](kubernetes-operator/README.md) | one observer at `memory_limit` 4G, 12Gi data and redo PVCs | empty kind → `running` OBCluster in ~4 min (cert-manager + image side-load 73 s, operator 52 s, OBCluster 131 s); OBTenant +29 s. kind worker 3.7 GiB; redo PVC held 12 GB preallocated. 3-zone not run: needs ~40 GB preallocated disk |

## Known issues

oceanbase-ce 4.4.2.1, 2026-10-02.

- **`OBD-1011: Insufficient AIO`**: obd refuses to start when `fs.aio-max-nr` (65536 by default in
  Docker Desktop's VM) is nearly used up, e.g. by ScyllaDB or a second OceanBase stack; six observers
  alone reached `fs.aio-nr` 55,152. Every example's `make up` runs `make aio-max-nr` first: a
  privileged one-shot `busybox` container raises it to 1048576 (`AIO_MAX_NR=...` to override,
  `AIO_MAX_NR=0` to leave it) and otherwise prints the current limit and usage. The setting is not
  namespaced: on Docker Desktop it applies to the whole VM until Docker restarts; on Linux it changes
  the host kernel.
- obd's disk check needs ~10 GB free in the Docker VM; `make up` fails on a nearly full disk.
- `MODE=mini`: a 2M-row load stalls at ~200k rows with the default memstore limit; htap-orders sets
  `memstore_limit_percentage = 50`.
- After an htap-orders run the `test` tenant's 1.5G log disk stays ~78% full and a second run in the
  same container loads ~10x slower (450 s vs 46 s). Use `make down up run`.
- Major compaction is slow on a laptop: ~6 min tenant-wide, 2.5–4 min for the 8 `orders` tablets.
- `ALTER RESOURCE POOL ... UNIT_NUM` fails with `ERROR 4179 ... zone_deploy_mode is 'homo', not 'hetero'`;
  use `ALTER RESOURCE TENANT <tenant> UNIT_NUM = n`. Scale in with `DELETE UNIT_GROUP (<id>)`, or the
  root service drops the original units and gets stuck migrating LS 1 (`ret:-4737, OB_LS_EXIST`)
  ([details](scale-out-in/README.md#scale-in)).
- Tenant CPU caps (`MAX_CPU`) need cgroups. The observer logs `check_cgroup_root_dir ... ret=-4027`,
  and 4.4.2.1 CE does not use cgroup v2 (`cgroup/cgroup.clone_children` not found), so `MAX_CPU`
  changes did not change throughput.
- obproxy keeps routing to deleted servers for ~40 s (`detect server dead`, `ret=-4015`); wait before
  stopping their containers.
- ob-operator 2.3.4 rejects small observers (`The minimum memory size of OBCluster is 8Gi`,
  `... data storage size ... is 30Gi`, `... redo log storage size ... is 30Gi`, `... log storage size ... is 10Gi`).
  Lower with `OB_OPERATOR_RESOURCE_MINMEMORYSIZE` / `MINDATADISKSIZE` / `MINREDOLOGDISKSIZE` /
  `MINLOGDISKSIZE` on the manager deployment; the message keeps printing the built-in values.
- `kind load docker-image` fails on the multi-arch `oceanbase-cloud-native` image
  (`ctr: content digest ...: not found`); save one platform and import it with `ctr`.
