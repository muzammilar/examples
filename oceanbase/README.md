# OceanBase

Website: https://en.oceanbase.com/

The examples use the MySQL-mode user tenant `test` (port 2881); cluster-wide views are
read from the `sys` tenant.

- [`single-node/`](single-node) — one observer from the `oceanbase/oceanbase-ce` image (`MODE=mini`) on Docker Compose.

- [`docker-compose-cluster/`](docker-compose-cluster) — three observers in three zones (zone1–3) on Docker Compose,
  bootstrapped by hand without obd; tenant `test` has locality `F@zone1, F@zone2, F@zone3`, with a
  `make failover` that kills the leader's observer.

- [`scale-out-in/`](scale-out-in) — the same cluster grown from 3 to 6 observers and shrunk back
  (`ALTER SYSTEM ADD/DELETE SERVER`, `ALTER RESOURCE TENANT test UNIT_NUM = 2/1`), with obproxy
  and a sysbench load running through it. It also resizes the tenant in place with
  `ALTER RESOURCE UNIT`. Observers at `memory_limit` 4G so six fit in a 24 GB Docker VM.

The cluster needs about 20 GB of Docker memory (3 × 6G `memory_limit` plus overhead) and ~20 GB of
disk. OceanBase's own recommendations for clusters are `obd` or
[ob-operator](https://github.com/oceanbase/ob-operator); ob-operator 2.3.4 refuses observers below
8Gi memory and 30Gi data + 30Gi redo storage each, which does not fit on a laptop.

## Benchmark

sysbench on one `mini` observer, 4 CPUs / 8 GB (memory raised from the usual 6 GB, because the observer's `memory_limit` floor is 6G; Apple M4 Pro, Docker VM aarch64, 2026-09-28): point selects 82k/s at p95 0.6 ms with 32 threads, `oltp_read_only` 4.6k tps (74k qps). `oltp_read_write` peaks at ~960 tps with 8 threads and halves at 32. Reads scale on the capped CPUs, while writes saturate early. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).

Cluster (3 observers, 2 CPUs / 7 GB each, all leaders in zone1, sysbench at 32 threads): point selects
40k/s, `oltp_read_write` ~900 tps. Killing the leader's observer moved leadership to zone2 and the
next write committed ~4.6 s after the kill. Full tables: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).

Scale out/in (`scale-out-in/`, 16 sysbench threads through obproxy, 1-CPU / 1.5G units): adding a
second observer and unit per zone split the user log stream in two and transferred 8 of 20
partitions in 2 s (balance job 30 s). Removing them merged the log streams back, and the whole
scale-in took 84 s. Both ran online with no failed statements, at the cost of one 5–10 s dip each. At 6 servers point selects
went from 54k to 57k qps through obproxy, but fell from 49k to 26k qps when sent to ob1 only,
which then forwards half of them. `oltp_read_write` fell from 651 to 366 tps because most
transactions then span both log streams (two-phase commit). Changing the unit's CPU from 1 to 3
did not change throughput: there is no cgroup in these containers. A 1G unit stalled the tenant.
Details: [`scale-out-in/README.md`](scale-out-in/README.md).

## Known issues

- OceanBase 4.4: `ALTER RESOURCE POOL ... UNIT_NUM` fails with `ERROR 4179 ... zone_deploy_mode is
  'homo', not 'hetero'`; use `ALTER RESOURCE TENANT <tenant> UNIT_NUM = n`. Scale in with
  `DELETE UNIT_GROUP (<id>)`: without it the root service dropped the original units and got stuck
  migrating the tenant's LS 1 (`ret:-4737, OB_LS_EXIST`) ([`scale-out-in/`](scale-out-in/README.md#scale-in)).
- Six observers exhaust the Docker VM's default `fs.aio-max-nr` (65536; `fs.aio-nr` reached
  55,152). Raise it with `docker run --rm --privileged alpine sysctl -w fs.aio-max-nr=1048576`.
- Tenant CPU caps (`MAX_CPU`) need cgroups. In these containers the observer logs
  `check_cgroup_root_dir ... ret=-4027`, and 4.4.2.1 CE does not use cgroup v2 (`cgroup/cgroup.clone_children`
  not found), so `MAX_CPU` changes did not change throughput.
- obproxy keeps routing to deleted servers for ~40 s (`detect server dead`, `ret=-4015`).
  Wait before stopping their containers.
