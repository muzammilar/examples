# OceanBase — 3-zone cluster with Docker Compose

Three OceanBase CE observers in three zones, bootstrapped by hand without obd, with a MySQL-mode
tenant `test` replicated to every zone and a leader-failover demo.

## Quick start

```bash
make up        # start ob1..ob3, bootstrap the cluster, create tenant `test` (about 40 s)
make test      # sql/*.sql: partitions, transaction, EXPLAIN, replicas/leaders per zone, cluster views
make failover  # kill the observer with the leaders, write through the others, restart it, wait for catch-up
make benchmark # sysbench OLTP against tenant `test` at 2 CPUs / 7 GB per observer; SMOKE=1 = quick
make status    # servers, tenants, `test` log stream leaders
make cli       # interactive obclient as root@test (on ob1)
make cli-sys   # interactive obclient as root@sys (cluster-wide views)
make logs      # container output + recent errors from each observer.log
make down      # remove the containers and their data
```

Three OceanBase Community Edition observers, one per zone, from the official
[`oceanbase/oceanbase-ce`](https://github.com/oceanbase/docker-images/tree/main/oceanbase-ce)
image (`4.4.2.1-101000022026050611`, tag `4.4.2-lts`, multi-arch, so native on Apple silicon).
They form one cluster with a MySQL-mode user tenant `test` that has a full Paxos replica in
every zone (`LOCALITY = 'F@zone1, F@zone2, F@zone3'`), so it survives losing any one observer.

No obd and no SSH: the image's own entrypoint (obd deploying one server) is replaced by
[`scripts/observer.sh`](scripts/observer.sh), which starts the `observer` binary that ships in
the image in the foreground, with the same command line obd would build
(`-z zone -r rootservice-list -n obcluster -c 1 -d store -o memory_limit=6G,...`). `make up`
then runs [`scripts/bootstrap.sh`](scripts/bootstrap.sh) once, which is the manual deployment
from the OceanBase docs:

```sql
ALTER SYSTEM BOOTSTRAP ZONE 'zone1' SERVER '172.28.10.11:2882',
                       ZONE 'zone2' SERVER '172.28.10.12:2882',
                       ZONE 'zone3' SERVER '172.28.10.13:2882';
CREATE RESOURCE UNIT test_unit MAX_CPU = 2, MIN_CPU = 2, MEMORY_SIZE = '4G', LOG_DISK_SIZE = '2G';
CREATE RESOURCE POOL test_pool UNIT = 'test_unit', UNIT_NUM = 1, ZONE_LIST = ('zone1', 'zone2', 'zone3');
CREATE TENANT test RESOURCE_POOL_LIST = ('test_pool'), LOCALITY = 'F@zone1, F@zone2, F@zone3',
  PRIMARY_ZONE = 'zone1;zone2;zone3' SET ob_compatibility_mode = 'mysql', ob_tcp_invited_nodes = '%';
```

- MySQL protocol, one port per observer: `localhost:2881` (ob1), `:2891` (ob2), `:2901` (ob3)
  (`OB1_PORT` / `OB2_PORT` / `OB3_PORT`). Any observer accepts connections for any tenant and
  forwards statements to the leader; there is no obproxy here, so a client pinned to one observer
  loses its connection when that observer goes down.
  - `mysql -h127.0.0.1 -P2881 -uroot@test` — user tenant `test`, no password
  - `mysql -h127.0.0.1 -P2881 -uroot@sys` — sys tenant, no password (cluster administration)
- `sql/*.sql` run as `root@test`; `sql/*.sys.sql` run as `root@sys`.
- The observers talk to each other by IP (rootservice list, `BOOTSTRAP ... SERVER`), so they get
  fixed addresses `172.28.10.11-13` on the compose network `172.28.10.0/24`.

## Topology

| Container | Zone | Address | Units | Leaders (steady state) |
|---|---|---|---|---|
| `ob1` | zone1 | 172.28.10.11 | sys 1 CPU / 1G, test 2 CPU / 4G | `test` + `META$1002` log streams; root service (first bootstrap zone) |
| `ob2` | zone2 | 172.28.10.12 | same | followers |
| `ob3` | zone3 | 172.28.10.13 | same | followers |

`PRIMARY_ZONE = 'zone1;zone2;zone3'` is a priority list: leaders stay in zone1 while it is up,
then zone2. A 4 GB tenant unit gets one user log stream (LS 1001) that holds every user tablet;
`PRIMARY_ZONE = RANDOM` would try to create one user log stream per zone (leaders spread over
the three observers), but on these small units the observers refuse the extra log streams with
`-4720 too many ls` and the tenant stays in `CREATING` (tried with 3G and 4G units).
`sql/04-locations.sql` shows the result (`make test`):

```
+-------+-------+--------------+----------+--------------+----------------------+-------------------------------------------------------------+
| LS_ID | ZONE  | SVR_IP       | ROLE     | REPLICA_TYPE | PAXOS_REPLICA_NUMBER | MEMBER_LIST                                                 |
+-------+-------+--------------+----------+--------------+----------------------+-------------------------------------------------------------+
|     1 | zone1 | 172.28.10.11 | LEADER   | FULL         |                    3 | 172.28.10.11:2882:1,172.28.10.12:2882:1,172.28.10.13:2882:1 |
|     1 | zone2 | 172.28.10.12 | FOLLOWER | FULL         |                 NULL | NULL                                                        |
|     1 | zone3 | 172.28.10.13 | FOLLOWER | FULL         |                 NULL | NULL                                                        |
|  1001 | zone1 | 172.28.10.11 | LEADER   | FULL         |                    3 | 172.28.10.11:2882:1,172.28.10.12:2882:1,172.28.10.13:2882:1 |
|  1001 | zone2 | 172.28.10.12 | FOLLOWER | FULL         |                 NULL | NULL                                                        |
|  1001 | zone3 | 172.28.10.13 | FOLLOWER | FULL         |                 NULL | NULL                                                        |
+-------+-------+--------------+----------+--------------+----------------------+-------------------------------------------------------------+
+-------+-----------------+---------+
| ZONE  | tablet_replicas | leaders |      (the 8 partitions of demo.accounts + demo.orders)
+-------+-----------------+---------+
| zone1 |               8 |       8 |
| zone2 |               8 |       0 |
| zone3 |               8 |       0 |
+-------+-----------------+---------+
```

`DBA_OB_TABLE_LOCATIONS` lists each partition three times (one FULL replica per zone), and
`GV$OB_LOG_STAT` shows every follower `IN_SYNC = YES`. `sql/05-cluster.sys.sql` shows the zones,
servers, tenants with their locality, per-server capacity, the units per zone and the leader zone
of every log stream of every tenant.

## Failover

`make failover` ([`scripts/failover.sh`](scripts/failover.sh)) finds the observer that leads
LS 1001 (ob1), `docker kill`s it (SIGKILL, no clean shutdown), and retries an `INSERT` into
tenant `test` through ob2 with a 2 s statement timeout until it commits. Then it shows the new
leaders, the server going `INACTIVE` and the root service moving, starts the container again
(the observer restarts from its data directory and saved config), waits until it is `ACTIVE` with
all replicas in sync, and waits for the leaders to switch back to zone1. `FAILOVER_NODE=ob2` (or
`ob3`) kills a follower instead. From a run here:

```
==> ob1 killed (SIGKILL); retrying an INSERT through ob2 until it commits
    attempt 1: ERROR 4012 (HY000) at line 1: Timeout, query has reached the maximum query timeout: 2000000(us), ...
==> INSERT committed 4665 ms after the kill (2 failed attempts), with 2 of 3 zones up
==> during: leaders moved off 172.28.10.11
+-------+-------+--------------+----------+
| LS_ID | ZONE  | SVR_IP       | ROLE     |
+-------+-------+--------------+----------+
|     1 | zone1 | 172.28.10.11 | FOLLOWER |
|     1 | zone2 | 172.28.10.12 | LEADER   |
|     1 | zone3 | 172.28.10.13 | FOLLOWER |
|  1001 | zone1 | 172.28.10.11 | FOLLOWER |
|  1001 | zone2 | 172.28.10.12 | LEADER   |
|  1001 | zone3 | 172.28.10.13 | FOLLOWER |
+-------+-------+--------------+----------+
+--------------+-------+----------+-----------------+
| SVR_IP       | ZONE  | STATUS   | WITH_ROOTSERVER |
+--------------+-------+----------+-----------------+
| 172.28.10.11 | zone1 | INACTIVE | NO              |
| 172.28.10.12 | zone2 | ACTIVE   | YES             |
| 172.28.10.13 | zone3 | ACTIVE   | NO              |
+--------------+-------+----------+-----------------+
==> starting ob1 again; waiting until it is ACTIVE and all replicas are in sync
==> ob1 rejoined after 9002 ms; replicas in sync
==> leaders of test and META$1002 back in zone1: 3 of 3 log streams, after 2239 ms
```

About 4–5 s without a writable leader: the old leader's lease has to run out before zone2 is
elected (the committed data is on the surviving majority, so nothing is lost; RPO 0). The root
service (sys tenant, `PRIMARY_ZONE RANDOM`) moves to ob2 and stays there after ob1 rejoins; the
`test` and `META$1002` leaders go back to zone1. The script waits for that switch-back: a leader
switch rolls back transactions that are open on the old leader, and a `make benchmark` started in
the middle of it failed once with `error 6002 (Transaction rollbacked)`.

## Benchmark

`make benchmark` runs [sysbench](https://github.com/akopytov/sysbench) 1.0.20 (built from
[`bench/Dockerfile`](bench/Dockerfile) on Debian trixie; compose profile `bench`, on the cluster
network) as `root@test` against ob1 (`BENCH_HOST`), the observer with the leaders: it prepares
`sbtest` tables, runs `oltp_point_select`, `oltp_read_only` and `oltp_read_write` for a fixed time
at each thread count, prints TPS, QPS, average and p95 latency per run, then drops the tables.
Raw output and a JSON summary (versions, parameters, tenant unit, locality, leader zone, Docker VM,
applied limits) go to the gitignored `results/`.

| Variable | Default | `SMOKE=1` |
|---|---|---|
| `BENCH_TABLES` × `BENCH_SIZE` | 4 × 50,000 rows | 2 × 10,000 |
| `BENCH_TIME` | 60 s per run | 10 s |
| `BENCH_THREADS` | `1 8 32` | `4` |

**Resource budget.** [`bench/limits.sh`](bench/limits.sh) caps the three observers at
`BENCH_CPUS=6` / `BENCH_MEM=21g` in total, i.e. **2 CPUs and 7 GB each** (no swap), with
`docker update`, and restores the old limits afterwards (Docker cannot remove a memory limit from
a running container, so "unlimited" comes back as the Docker VM's total memory; `make down && make
up` starts clean). 7 GB is the observer's 6G `memory_limit` plus process overhead; 8 GB each would
be 24 GB of caps on a 24.4 GB Docker VM with nothing left for the client. The observers' resident
memory stayed around 2.8–3.2 GiB during the run. The sysbench client has `cpus: 2`
(`BENCH_CLIENT_CPUS`).

What it measures: every statement goes to ob1, which holds all leaders, so reads use ob1's
2 CPUs only, and every commit waits for the redo to be persisted on a majority (ob1 + the faster
of ob2/ob3) over the Docker network. Transactions still commit one-phase (one log stream). It is
Paxos-replicated OLTP on one leader, not load spread over three observers.

### Sample results

2026-09-28, `make benchmark` (defaults: 4 × 50,000 rows, 60 s per run) right after
`make up && make test && make failover && make test`, Docker Desktop 29.5.3 on an Apple M4 Pro
(Docker VM: 11 CPUs, 24.4 GB, aarch64, native arm64 image), OceanBase CE 4.4.2.1, tenant `test`
2 CPU / 4 GiB per zone × 3 zones, observers capped at 2 CPUs / 7 GB each, sysbench 1.0.20 on 2 CPUs.

| Workload | Threads | TPS | QPS | avg ms | p95 ms |
|---|---|---|---|---|---|
| oltp_point_select | 1 | 11,016 | 11,016 | 0.09 | 0.18 |
| oltp_point_select | 8 | 28,289 | 28,289 | 0.28 | 0.31 |
| oltp_point_select | 32 | 39,997 | 39,997 | 0.80 | 0.59 |
| oltp_read_only | 1 | 568 | 9,080 | 1.76 | 2.39 |
| oltp_read_only | 8 | 1,595 | 25,520 | 5.01 | 44.17 |
| oltp_read_only | 32 | 2,183 | 34,935 | 14.65 | 71.83 |
| oltp_read_write | 1 | 334 | 6,677 | 2.99 | 3.62 |
| oltp_read_write | 8 | 844 | 16,880 | 9.48 | 42.61 |
| oltp_read_write | 32 | 906 | 18,124 | 35.31 | 84.47 |

No errors. A second from-scratch cycle (`make down; make up && make test && make failover &&
make test && make benchmark`) matched within 5% (for example 40,074 point selects/s and 869
read-write tps at 32 threads), except the `oltp_read_only` 8-thread p95, which was 4.65 ms there:
the 44 ms p95 values come from occasional stalls, not from the typical request. Single-threaded, reads cost the same as on one observer (0.09 ms point select); a
read-write transaction takes 2.99 ms against 2.33 ms on the single-node example, the price of the
majority round trip. Reads stop scaling at about 40k point selects/s, bounded by ob1's 2 CPUs
(the single-node example had 4 CPUs and reached 82k). Writes, unlike on one observer, keep
their throughput at 32 threads (906 tps) instead of halving.

## Memory and disk

Settings are in [`docker-compose.yml`](docker-compose.yml) (`OB_OPTS`, the observer's `-o`
string, applied on first start) and `scripts/bootstrap.sh`; override with the variable names
(`OB_MEMORY_LIMIT=... make up`, `OB_TENANT_MEMORY=...`).

| Setting | Per observer | Notes |
|---|---|---|
| `memory_limit` | 6G | the smallest that bootstraps in the single-node example |
| `system_memory` | 1G | leaves 5G for tenant units |
| `__min_full_resource_pool_memory` | 1G | default 5G, which makes the sys unit 5G / 5G log disk, and bootstrap fails with `ERROR 4624: machine resource is not enough to hold a new unit`; obd sets 1G the same way on small hosts |
| sys unit | 1 CPU, 1G, 2G log disk | created by `BOOTSTRAP` |
| `test` unit | 2 CPU, 4G, 2G log disk | `OB_TENANT_CPU` / `OB_TENANT_MEMORY` / `OB_TENANT_LOG_DISK` |
| `cpu_count` | 4 | CPUs each observer schedules with (sys 1 + test 2 + headroom) |
| `datafile_size` | 2G | preallocated |
| `log_disk_size` | 4G | preallocated: sys 2G + test 2G |
| syslog | `WARN`, 2 rotated files | at `INFO` each observer wrote about 250 MB of logs in 3 minutes |

**Resources needed: Docker Desktop with at least 20 GB of memory** (3 × 6G `memory_limit` plus
overhead; the observers use about 2.6–3 GiB each after `make up`, but each may grow to its limit),
and about **20 GB of free disk** (6.1 GB of preallocated data file + log disk per observer, plus the
1.9 GB image). Tested on a 24.4 GB / 11 CPU Docker VM. Idle, each observer uses about 30% of a CPU.
Data lives in the container layers: it survives `docker kill`/`docker start` and is discarded by
`make down`.

Not set up here, compared with an obd deployment: obproxy (clients connect to an observer
directly), named time zones (obd imports the time zone tables into each tenant; here
`CONVERT_TZ(..., 'Asia/Shanghai')` returns NULL, offsets such as `'+00:00'` work), and passwords
(root has none in both tenants).

### Future work

Run one common sysbench workload set (same scripts, table count/size, thread counts and duration)
across every sysbench-capable example — TiDB, OceanBase (single node and cluster), SingleStore and
RonDB over the MySQL protocol, YugabyteDB YSQL and CedarDB with sysbench's `pgsql` driver — so their
numbers compare directly. Today each example uses its own parameters.

Also run one standardized benchmark everywhere: TPC-C (e.g. with [go-tpc](https://github.com/pingcap/go-tpc),
which speaks MySQL and PostgreSQL, or [BenchBase](https://github.com/cmu-db/benchbase)) with the same number
of warehouses, threads, duration and think-time setting for every system. The warehouse count sets the data
size and the contention: with the spec's keying/think times, throughput is capped at about 12.86 tpmC per
warehouse (the YDB example's 10-warehouse run reached 127 tpmC, i.e. that cap, not its limit), so a comparison
needs either enough warehouses or think time disabled, applied the same way to each database.
