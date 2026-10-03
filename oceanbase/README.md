# OceanBase

Website: https://en.oceanbase.com/

The examples use the MySQL-mode user tenant `test` (port 2881); cluster-wide views are
read from the `sys` tenant.

- [`single-node/`](single-node) — one observer from the `oceanbase/oceanbase-ce` image (`MODE=mini`) on Docker Compose.
- [`docker-compose-cluster/`](docker-compose-cluster) — three observers in three zones (zone1–3) on Docker Compose,
  bootstrapped by hand without obd; tenant `test` has locality `F@zone1, F@zone2, F@zone3`, with a
  `make failover` that kills the leader's observer.

- [`htap-orders/`](htap-orders) — a Go program on one `mini` observer (port 2891): OLTP
  transactions and analytics at the same time on one hybrid row/column table (`WITH COLUMN
  GROUP(all columns, each column)`). It compares row-store and column-store scans, alone and
  next to concurrent writes, and shows that a column-store aggregate sees orders committed a
  moment earlier.

- [`scale-out-in/`](scale-out-in) — the same cluster grown from 3 to 6 observers and shrunk back
  (`ALTER SYSTEM ADD/DELETE SERVER`, `ALTER RESOURCE TENANT test UNIT_NUM = 2/1`), with obproxy
  and a sysbench load running through it. It also resizes the tenant in place with
  `ALTER RESOURCE UNIT`. Observers at `memory_limit` 4G so six fit in a 24 GB Docker VM.

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

HTAP example (one `mini` observer, 6 CPUs, 2M orders in a hybrid row/column table, 2026-10-02): aggregations ran 5–170x faster through the column store than through the row copy of the same table. With 16 OLTP workers writing, 2 column-store analytics workers finished 3.6x more queries than row-store ones, and OLTP kept 74% of its solo ~1.5k tps (65% with row-store scans; a 2026-10-03 rerun kept 37% vs 29%, same ordering). Details: [`htap-orders/README.md`](htap-orders/README.md#sample-output).

Scale out/in (`scale-out-in/`, 16 sysbench threads through obproxy, 1-CPU / 1.5G units): adding a
second observer and unit per zone split the user log stream in two and transferred 8 of 20
partitions in 2 s (balance job 30 s). Removing them merged the log streams back, and the whole
scale-in took 84 s. Both ran online with no failed statements, at the cost of one 5–10 s dip each. At 6 servers point selects
went from 54k to 57k qps through obproxy, but fell from 49k to 26k qps when sent to ob1 only,
which then forwards half of them. `oltp_read_write` fell from 651 to 366 tps because most
transactions then span both log streams (two-phase commit). Changing the unit's CPU from 1 to 3
did not change throughput: there is no cgroup in these containers. A 1G unit stalled the tenant.
Details: [`scale-out-in/README.md`](scale-out-in/README.md).

ob-operator on kind (`kubernetes-operator/`, one observer at `memory_limit` 4G, 12Gi data and redo
log PVCs): from an empty kind cluster to a `running` OBCluster in about 4 minutes (cert-manager
and the image side-load 73 s, operator 52 s, OBCluster 131 s), and the OBTenant took 29 s more. The kind
worker used 3.7 GiB; the redo log PVC held 12 GB of preallocated log disk. A 3-zone cluster was
not run: it would need about 40 GB of preallocated disk. Details:
[`kubernetes-operator/README.md`](kubernetes-operator/README.md).

## Known issues

Seen while building these examples (oceanbase-ce 4.4.2.1, 2026-10-02):

- obd refuses to start the observer (`OBD-1011: Insufficient AIO`) when the kernel's
  `fs.aio-max-nr` (65536 by default in Docker Desktop's VM) is nearly used up, for example by
  ScyllaDB or a second OceanBase stack on the same VM. Every example's `make up` now runs
  `make aio-max-nr` first: a privileged one-shot `busybox` container raises it to 1048576 (override
  with `AIO_MAX_NR=...`, `AIO_MAX_NR=0` leaves it unchanged) and otherwise prints the current limit and usage.
  The setting is not namespaced: on Docker Desktop it applies to the whole Docker VM until Docker
  restarts, and on a Linux host it changes the host kernel.
- obd's disk check needs ~10 GB free in the Docker VM, so `make up` fails on a nearly full disk.
- In `MODE=mini`, a 2M-row load stalls at ~200k rows with the default memstore limit. The
  HTAP example sets `memstore_limit_percentage = 50`.
- The `test` tenant's 1.5G log disk stays ~78% full after an htap-orders run, and a second run in
  the same container loads ~10x slower (450 s vs 46 s). Use `make down up run`.
- Major compaction is slow on a laptop: ~6 min tenant-wide, 2.5–4 min for the 8 `orders`
  tablets.
- ob-operator 2.3.4 refuses observers below 8Gi memory and 30Gi data + 30Gi redo storage each.
- OceanBase 4.4: `ALTER RESOURCE POOL ... UNIT_NUM` fails with `ERROR 4179 ... zone_deploy_mode is
  'homo', not 'hetero'`; use `ALTER RESOURCE TENANT <tenant> UNIT_NUM = n`. Scale in with
  `DELETE UNIT_GROUP (<id>)`: without it the root service dropped the original units and got stuck
  migrating the tenant's LS 1 (`ret:-4737, OB_LS_EXIST`) ([`scale-out-in/`](scale-out-in/README.md#scale-in)).
- Six observers alone nearly exhaust the default `fs.aio-max-nr` (65536; `fs.aio-nr` reached
  55,152), which `make up` raises (see above).
- Tenant CPU caps (`MAX_CPU`) need cgroups. In these containers the observer logs
  `check_cgroup_root_dir ... ret=-4027`, and 4.4.2.1 CE does not use cgroup v2 (`cgroup/cgroup.clone_children`
  not found), so `MAX_CPU` changes did not change throughput.
- obproxy keeps routing to deleted servers for ~40 s (`detect server dead`, `ret=-4015`).
  Wait before stopping their containers.
- ob-operator 2.3.4 rejects small observers (`The minimum memory size of OBCluster is 8Gi`, `...
  data storage size ... is 30Gi`, `... redo log storage size ... is 30Gi`, `... log storage size ...
  is 10Gi`). Lower the floors with `OB_OPERATOR_RESOURCE_MINMEMORYSIZE` / `MINDATADISKSIZE` /
  `MINREDOLOGDISKSIZE` / `MINLOGDISKSIZE` on the manager deployment. The message keeps printing
  the built-in values.
- `kind load docker-image` fails on the multi-arch `oceanbase-cloud-native` image (`ctr: content
  digest ...: not found`). Save one platform and import it with `ctr` instead.
