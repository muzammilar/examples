# OceanBase: scale out, scale in and tenant resize on Docker Compose

Grow a 3-zone OceanBase CE cluster from 3 to 6 observers and back while it serves load, behind
obproxy, and resize the tenant's unit.

## Quick start

```bash
make up         # ob1..ob3 + bootstrap + tenant `test` (locality F@zone1,F@zone2,F@zone3) + obproxy, ~30 s
make test       # sql/*.sql: a 12-partition table, where its partitions and leaders are, cluster views
make scale-out  # start ob4..ob6, ADD SERVER, UNIT_NUM 2, follow the balance job until it is done
make scale-in   # UNIT_NUM 1 DELETE UNIT_GROUP (the new units), DELETE SERVER, remove ob4..ob6
make demo       # sysbench load running through scale-out and scale-in; tps per phase
make resize     # ALTER RESOURCE UNIT (CPU / memory) with a sysbench run at each size
make status     # servers, units, log streams with leaders, tablets per log stream
make cli        # obclient as root@test (on ob1); make cli-sys for root@sys
make down       # remove everything
```

The [`docker-compose-cluster`](../docker-compose-cluster) setup (OceanBase CE `4.4.2.1`, observers
started without obd by [`scripts/observer.sh`](scripts/observer.sh), bootstrapped by
[`scripts/bootstrap.sh`](scripts/bootstrap.sh)), extended with:

- **ob4..ob6**, a second observer for each of zone1..zone3 (compose profile `scale`), which
  `make scale-out` adds to the running cluster and `make scale-in` removes again: 3 → 6 → 3
  observers.
- **obproxy** (`oceanbase/obproxy-ce:4.3.5.0-3`, port 2883), which sends every statement to
  the observer that leads the partition it touches. Without it a client is pinned to one observer.
- smaller observers so six of them fit in a 24 GB Docker VM: `memory_limit` 4G (the cluster
  example uses 6G), 3 CPUs and 5 GB per container, and a `test` unit of 1 CPU / 1.5G.

- `mysql -h127.1 -P2883 -uroot@test#obcluster` goes through obproxy; `-P2881 -uroot@test`
  goes to ob1 directly. No passwords. The sys-tenant user `proxyro` (password `proxyro`,
  `OB_PROXYRO_PASSWORD`) is what obproxy reads the partition locations with.
- Fixed addresses on `172.28.12.0/24`: ob1..ob3 `.11-.13`, ob4..ob6 `.14-.16`, obproxy `.20`.

## Scale out

[`scripts/scale.sh out`](scripts/scale.sh) does what an operator does by hand:

```sql
-- after `docker compose --profile scale up ob4 ob5 ob6` (they start with the same rootservice list)
ALTER SYSTEM ADD SERVER '172.28.12.14:2882' ZONE 'zone1';   -- and .15 / zone2, .16 / zone3
ALTER RESOURCE TENANT test UNIT_NUM = 2;                     -- one more unit of `test` per zone
```

Adding servers alone moves nothing: a tenant only uses the servers its units sit on. Raising
`UNIT_NUM` puts a second unit in each zone on the new servers, and the tenant's balancer then
runs an `LS_BALANCE` job. It splits the user log stream LS 1001, moves half of the partitions into
the new LS 1002 with a transfer, and moves LS 1002's replicas onto the new units. In OceanBase 4.4
the statement is `ALTER RESOURCE TENANT`. The older form fails here with
`ERROR 4179: Tenant 1002 zone_deploy_mode is 'homo', not 'hetero', alter resource pool unit_num not allowed`.

From the `make demo` run (times in seconds since the load started, sysbench running):

```
[  60.2s] scale out: starting ob4 ob5 ob6 (second observer in each zone)
[  75.0s] 6 servers ACTIVE
[  75.1s] ALTER RESOURCE TENANT test UNIT_NUM = 2
[  77.4s] units 1004..1006:ADDING | ls 1001[1001,1002,1003] 1002[1001,1002,1003]CREATING | LS_SPLIT 1001->1002:CREATE_LS
[  87.3s] ...                                                                          | LS_SPLIT 1001->1002:TRANSFER
[  91.1s] ...                                                                          | LS_ALTER 1002->-1:ALTER_LS
[  97.2s] ls 1001[1001,1002,1003] 1002[1004,1005,1006]      (LS 1002 now lives on ob4..ob6)
[ 108.3s] units 1001..1006:ACTIVE, no balance job left
```

```
JOB  LS_BALANCE  unit list balance  zone1:2,zone2:2,zone3:2  COMPLETED  30034 ms
TASK LS_SPLIT 1001 -> 1002  8 partitions  13080 ms  |  LS_ALTER 1002  15100 ms
TRANSFER 1001 -> 1002  8 partitions  2010 ms

+-------+----------------+--------------+     +------------+---------+-------+
| LS_ID | UNIT_LIST      | user_tablets |     | TABLE_NAME | tablets | LS_ID |
+-------+----------------+--------------+     +------------+---------+-------+
|  1001 | 1001,1002,1003 |           10 |     | orders     |       6 |  1001 |
|  1002 | 1004,1005,1006 |           10 |     | orders     |       6 |  1002 |
+-------+----------------+--------------+     | sbtest1/2  |       1 |  1001 |
                                              | sbtest3/4  |       1 |  1002 |
                                              +------------+---------+-------+
```

The 12 hash partitions of `demo.orders` (from `make test`) and the four sysbench tables were split
evenly across the two log streams: 8 partitions moved, the other 12 stayed. Leaders stay in
zone1 (`PRIMARY_ZONE 'zone1;zone2;zone3'`). Once the migration has finished, LS 1001 is led
from ob1 and LS 1002 from ob4 (`DBA_OB_LS_LOCATIONS`, checked by hand in an earlier run). At the
moment the job reported done, the LS 1002 leader still showed as ob1.

## Scale in

```sql
ALTER RESOURCE TENANT test UNIT_NUM = 1 DELETE UNIT_GROUP (1002);   -- the units on ob4..ob6
ALTER SYSTEM DELETE SERVER '172.28.12.14:2882', '172.28.12.15:2882', '172.28.12.16:2882';
```

The balancer merges LS 1002 back into LS 1001 (`LS_MERGE`, 8 partitions transferred in 15.9 s,
job 40.1 s). The units go away 50 s after the statement. `DELETE SERVER` then takes about 1 s,
because nothing of any tenant is left on those servers. The script waits 30 s (`DRAIN`) before it
stops the containers (see Known issues). The whole scale-in took 84 s.

Leave out `DELETE UNIT_GROUP` and the root service picks which units to drop. Here it picked the
**original** units on ob1..ob3. It then had to migrate LS 1 (the tenant's system log stream,
leader on ob1) to ob4, and it hung: the migration retried every ~10 s and failed each time with
`ret:-4737, OB_LS_EXIST; comment:[storage] fail to send execution rpc` (`CDB_OB_LS_REPLICA_TASK_HISTORY`,
`migrate replica due to unit deleting`, for tenants 1002 and 1001). The units stayed `DELETING`
for over 5 minutes. `ALTER RESOURCE TENANT test UNIT_NUM = 2` rolled it back within about 15 s.
Name the unit group.

## Load during scaling (`make demo`)

sysbench 1.0.20 (2 CPUs) with 16 threads through obproxy against 4 tables × 50,000 rows,
`--mysql-ignore-errors=all`, reporting every 5 s. 60 s before, between and after. Averages per phase
from one run each, 2026-10-03, Apple M4 Pro, Docker VM 11 CPUs / 24.4 GB, other agents' workloads
running on the same Mac:

| Load | 3 servers before | scaling out | 6 servers | scaling in | 3 servers after |
|---|---|---|---|---|---|
| `oltp_read_write`, obproxy | 651 tps (p95 max 92 ms) | 569 (min 98) | 366 (p95 max 258 ms) | 346 (min 123) | 634 |
| `oltp_point_select`, obproxy | 54.0k qps | 30.5k (min 1.0k) | **57.1k** | 43.7k (min 1.4k) | 52.9k |
| `oltp_point_select`, ob1 direct | 49.0k qps | 28.1k (min 0.9k) | **26.0k** | 43.0k (min 2.8k) | 55.5k |

No statement failed in any run (err/s 0.00). What the numbers show:

- **Both scale-out and scale-in are online.** Each one costs one 5–10 s dip: about 1k qps, or
  ~100 tps for read-write. It comes when the transfer switches partitions between log streams
  (and, on scale-out, when LS 1002's replicas migrate). The rest of the time throughput stays
  near its level.
- **Reads, through obproxy: about the same (54k → 57k qps).** The point selects are split
  between ob1 and ob4 now, but the 2-CPU sysbench client and the single obproxy were already
  the limit at 3 servers, so the second leader adds little here.
- **Reads, all sent to ob1: halved (49k → 26k qps).** Half of the partitions are now led by ob4,
  so ob1 forwards every second query to it. Without a routing proxy, a scale-out makes a
  pinned client slower.
- **Read-write transactions: slower (651 → 366 tps).** sysbench picks a random table for each
  statement, so most transactions now touch both log streams and commit with two-phase commit
  across ob1 and ob4 instead of one-phase in one log stream. Scaling out pays off for work that
  stays inside one log stream (use table groups or the same partition key), not for 4 small
  tables shared by every transaction.
- These are single short runs on a shared laptop. `oltp_read_write` with 1.5G units varies a lot
  between 5 s intervals (270–1,100 tps before scaling) because the tenant freezes its memstore
  about every 64 MB.

## Tenant resize (`make resize`)

```sql
ALTER RESOURCE UNIT test_unit MIN_CPU = 3, MAX_CPU = 3, MEMORY_SIZE = '1536M';
```

This changes all three units of `test` in place, with no data movement. `GV$OB_UNITS` showed the new
spec on every server 4–5 s later (0.2 s when nothing changed). Results, 32 threads through ob1,
30 s each, in the order they ran:

| Unit | oltp_point_select qps | oltp_read_write tps | p95 ms (point / rw) |
|---|---|---|---|
| 1 CPU, 1.5G | 65,734 | 1,502 | 0.58 / 38.3 |
| 3 CPU, 1.5G | 57,666 | 990 (42 ignored errors) | 0.64 / 63.3 |
| 1 CPU, 1.5G | 55,175 | 1,067 | 0.72 / 55.8 |
| 1 CPU, 1G | stalled, see below | | |

**Changing CPU did not change throughput here.** The decline from row to row is drift over time
(the same 1-CPU spec measured 1,502 and then 1,067 tps), not the unit size. The observer only caps
a tenant's CPU with cgroups, and none are set up in these containers. `observer.log` has
`check_cgroup_root_dir ... dir not exist(OBSERVER_ROOT_CGROUP_DIR="cgroup", ret=-4027)`.
`MAX_CPU` still sizes the tenant's worker pool (`cpu_quota_concurrency` 10 active threads per CPU),
but 10 workers already fill the container's 3 CPUs. A privileged container with a writable
`/sys/fs/cgroup` (cgroup v2, kernel 6.12) did not help either. This 4.4.2.1 CE observer only writes
cgroup v1 files and failed with
`open file error(filename="cgroup/cgroup.clone_children", errno=2 ...)`. OceanBase's 4.4 release notes
mention cgroup v2 support, but this build does not have it. On a Linux host with cgroup v1 for the
observer, `MAX_CPU` would cap the CPU.

**Memory is a hard limit.** A user tenant's unit memory is shared with its meta tenant
(`GV$OB_UNITS`: 1.5G = 0.75G for tenant 1002 + 0.75G for meta tenant 1001; at 1G it is
0.5G + 0.5G). At 1G the memstore limit dropped from about 410 to 341 MB (freeze trigger 53 MB). The next
sysbench point-select run hung for over 15 minutes. `observer.log` repeated
`Transaction commit cost too much time ... errcode=-6281`, and the cleanup failed with
`errno = 4013 (No memory or reach tenant memory limit)`, while tenant 1002 held 702 MB against
its 512 MB share. In an earlier run at 1G, point selects failed straight away with
`error 4013 (No memory or reach tenant memory limit)`. With this schema and 32 connections, 1.5G
is the smallest unit that worked. The default `RESIZE_STEPS` therefore stops at 1.5G.

**Growing memory back can fail.** After a shrink (2G → 1.5G in an earlier run), growing back to 2G
failed for over 2 minutes with
`ERROR 4624: zone 'zone1' server '"172.28.12.11:2882"' MEMORY_SIZE resource is not enough to hold a new unit`.
`GV$OB_SERVERS.MEM_ASSIGNED` on ob1 was 2.63 GiB, against 2.5 GiB of unit specs (sys 1G + test
1.5G), on a 3 GiB `MEM_CAPACITY`. 1792M worked. Leave headroom: that is why `test` starts at
1.5G and not at the 2G that fits. `resize.sh` retries on 4624 for up to 2 minutes.

## Memory and disk

| | Per observer |
|---|---|
| `memory_limit` / `system_memory` | 4G / 1G: 3G for units, sys 1G + `test` 1.5G + 0.5G spare |
| container | `cpus: 3` (`OB_CPUS`), `mem_limit` 5g (`OB_CONTAINER_MEM`) |
| `cpu_count` | 4 (sys 1 + test up to 3) |
| `datafile_size` / `log_disk_size` | 2G / 4G, preallocated |

Measured: about 2.6–3.1 GiB resident per observer after `make up`, 3.7 GiB on ob1 after the
runs. obproxy uses about 240 MB. Six observers at 4G each fit in the 24.4 GB Docker VM. The 6G of the
cluster example would not. The VM's `fs.aio-max-nr` (65536) runs out with six observers:
`fs.aio-nr` reached 55,152 with ob1..ob6 started. `make up` raises it to 1048576 first
(`make aio-max-nr`, see [`../README.md`](../README.md#known-issues)). Disk: about 6.1 GB preallocated per observer, up to 37 GB with six, plus the 1.9 GB image.

## Known issues

- Six observers need about 37 GB free in the Docker VM. On 2026-10-03 the shared VM had about 28 GB
  free before `make up`, and `make scale-out` failed twice: ob6 (and in the second try ob4 too)
  exited with code 240, `fallocate failed` on its log pool, `maybe available disk size(0MB) is not
  enough to satisfy new log_disk_size(4096MB)`, `OB_MACHINE_RESOURCE_NOT_ENOUGH`. `make scale-out`
  now stops after ~20 s and prints that line for each failed observer instead of timing out on
  `ADD SERVER`. Free disk in the Docker VM, then `make down up`.
  Shrinking `OB_LOG_DISK_SIZE` to 3G does not help: `make up` then fails creating `test` with
  `LOG_DISK resource not enough`, because the sys tenant takes 2G of it.
- `ALTER RESOURCE POOL test_pool UNIT_NUM = 2` fails on 4.4 with `ERROR 4179 ... zone_deploy_mode
  is 'homo', not 'hetero'`. Use `ALTER RESOURCE TENANT test UNIT_NUM = n`.
- Scale-in without `DELETE UNIT_GROUP` dropped the original units and got stuck migrating LS 1
  (`OB_LS_EXIST`, see Scale in). Raising `UNIT_NUM` again rolled it back.
- obproxy keeps routing to removed servers for a while. With the containers stopped right after
  `DELETE SERVER`, obproxy logged `detect server dead(... 172.28.12.14:2881 ...)` and
  `handle_connect ... ret=-4015`, and read-write throughput fell to 6 tps (p95 5.1 s) for about 40 s.
  `scale.sh` now waits `DRAIN=30` s first.
- Tenant CPU isolation is inactive in these containers (no cgroup v1, see Tenant resize). A 1G
  `test` unit stalls (4013 / -6281). Growing memory right after a shrink can fail with 4624.
- `make demo` uses `--mysql-ignore-errors=all` so that it shows errors instead of stopping. In
  these runs it counted none during scaling. The one `make resize` step with 42 ignored errors
  was `oltp_read_write` at 3 CPUs.
