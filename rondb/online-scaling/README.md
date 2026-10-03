# RonDB — online scaling with Docker Compose

What RonDB can scale while it runs, shown on one cluster that starts like
[`../docker-compose-cluster/`](../docker-compose-cluster) (1 management server, node group 0 =
data nodes 1 + 2 with `NoOfReplicas=2`, 1 MySQL Server) and grows from there.

```bash
make all             # every step below in order, with an oltp_read_write load at each (~12 min)
make up              # mgmd + data nodes 1, 2 (node group 0) + mysqld-1; nodes 3, 4 deactivated
make load            # sysbench prepare: 4 x 50,000 rows ENGINE=NDB, then make dist
make baseline        # oltp_read_write 30 s through mysqld-1 on 2 data nodes
make add-nodegroup   # activate nodes 3, 4, start them, CREATE NODEGROUP 3,4 (and drop/create it while empty)
make reorg           # REORGANIZE PARTITION + OPTIMIZE TABLE on all 4 tables during a 120 s load
make after           # oltp_read_write 30 s on 4 data nodes
make add-api         # start mysqld-2 + rest, a REST pk-read, load through both MySQL Servers
make drop-nodegroup  # DROP NODEGROUP 1 (fails), deactivate/reactivate node 3
make scale-up        # config-scale-up.ini, mgmd --reload, rolling restart under a 120 s load
make dist            # fragments of sbtest1 per data node, DataMemory used per node
make status / cli     # ndb_mgm show + memory report / mysql client on mysqld-1
make down            # remove containers, volumes and the bench image
```

The steps:

| step | what changes | how | restart? |
|------|--------------|-----|----------|
| `add-nodegroup` | data nodes 2 → 4: nodes 3 + 4 become node group 1 | `ndb_mgm -e "3 activate"`, start them, `CREATE NODEGROUP 3,4` | none |
| `reorg` | existing tables spread over both node groups | `ALTER TABLE ... ALGORITHM=INPLACE, REORGANIZE PARTITION`, `OPTIMIZE TABLE` | none |
| `add-api` | a second MySQL Server + the REST API server join | start them in spare `[MYSQLD]` / `[API]` slots | none |
| `drop-nodegroup` | scale in: data nodes 4 → 2 | `DROP NODEGROUP 1`: **refused**, the node group holds data | n/a |
| `scale-up` | `DataMemory` 768M → 1G, `NumCPUs` 2 → 3 per data node | new config.ini, management server `--reload` | rolling restart of the data nodes |

- MySQL: `127.0.0.1:3310` (mysqld-1) and `:3311` (mysqld-2), user `rondb` / `rondb`; REST API
  `127.0.0.1:4407`. Image `hopsworks/rondb:26.02.11` (amd64 + arm64).
- Each data node is capped at 2 CPUs / 2 GB (`docker update` raises it to 3 CPUs / 2.5 GB in
  `scale-up`), each MySQL Server at 2 CPUs. All 4 data nodes, 2 MySQL Servers, the REST server and
  the sysbench client run on one Docker VM (~7 GB at the end).
- [`config/config.ini`](config/config.ini) already lists every node the cluster will ever have:
  nodes 3 + 4 with `NodeGroup=65536` (no node group, so no data) and `NodeActive=0`, MySQL Server
  slot 68 and REST slot 195. So none of the scale-out steps needs a new config.ini or a restart.
  Data nodes set their memory pools by hand (`AutomaticMemoryConfig=false`, `DataMemory=768M`).
- `ndb_mgmd` copies `config/config.ini` into its container on the first start (`--initial`) and
  keeps its config cache there; later starts use `--reload`. The `N activate` state lives in that
  cache, so the Makefile never lets compose recreate the `mgmd` container (`--no-deps`), and
  `config-scale-up.ini` says `NodeActive=1` for nodes 3 + 4.
- The load is sysbench 1.0.20 `oltp_read_write`, 16 threads, all errors ignored and counted
  ([`bench/sb.sh`](bench/sb.sh)), with 5 s interval reports; [`bench/series.sh`](bench/series.sh)
  prints them with the operations placed at the second they happened.

## Scale out: add a node group

```
$ ndb_mgm -e "3 activate"
Configuration changed to reflect activated node
Now activating the node in the cluster
Node 3 is now activated in the cluster
$ ndb_mgm -e show            # after starting ndbd-3, ndbd-4 (initial start, ~11 s)
id=3	@172.19.0.6  (RonDB-26.02.11, no nodegroup)
id=4	@172.19.0.7  (RonDB-26.02.11, no nodegroup)
$ ndb_mgm -e "CREATE NODEGROUP 3,4"
Nodegroup 1 created          # 0.15 s
```

`CREATE NODEGROUP` takes 0.15 s, and the new nodes hold nothing yet: existing tables stay in node
group 0 (4 partitions, 50,000 rows on each of nodes 1 + 2). New tables use both node groups.
While node group 1 is still empty, `DROP NODEGROUP 1` works (`Drop Node Group 1 done`). The target
drops it and creates it again.

## Reorganise existing tables

`ALTER TABLE sbtestN ALGORITHM=INPLACE, REORGANIZE PARTITION` doubles each table's partitions
(4 → 8) and moves half of the rows into node group 1. `OPTIMIZE TABLE` then compacts the old
fragments. With 16 sysbench threads writing through mysqld-1 (2026-10-03, 5 s intervals):

```
            >>>   20s ALTER TABLE sbtest1 REORGANIZE PARTITION start
            >>>   28s ALTER TABLE sbtest1 done
            ...
            >>>   44s ALTER TABLE sbtest4 REORGANIZE PARTITION start
   15s  tps    792.0  p99    70.55 ms  err/s   0.00
   20s  tps    773.6  p99    70.55 ms  err/s   0.80
   25s  tps      0.0  p99     0.00 ms  err/s   0.00
   30s  tps     22.4  p99  7895.16 ms  err/s   0.80
   35s  tps      0.0  p99     0.00 ms  err/s   0.00
   40s  tps     18.0  p99  8038.61 ms  err/s   1.20
   45s  tps     18.2  p99  7895.16 ms  err/s   0.40
   50s  tps      0.0  p99     0.00 ms  err/s   0.00
            >>>   52s ALTER TABLE sbtest4 done
            >>>   53s OPTIMIZE TABLE x4 done
   55s  tps    426.4  p99    71.83 ms  err/s   1.80
   60s  tps    829.0  p99    65.65 ms  err/s   0.00
```

Each `ALTER` took ~8 s for 50,000 rows. The data stayed readable and the ALTER never failed, but
**the write load stalled while the tables were reorganised**: transactions waited up to ~8 s (p99
7.9 s) and throughput fell to 0–22 tps for the 32 s of the four ALTERs, then came back at once.
25 transactions failed (sysbench does not print which error). Afterwards each data node holds
about a quarter of the rows (nodes 1 + 2: 25,056 rows of `sbtest1`, nodes 3 + 4: 24,944), but
`DataMemory` used on nodes 1 + 2 stayed at 161 MiB after `OPTIMIZE TABLE` (158 MiB before; nodes
3 + 4: 78 MiB). The moved rows' pages were not returned within this run.

## More API nodes

`mysqld-2` and `rest` start into the spare slots 68 and 195 and show up in `ndb_mgm -e show`. A REST
`pk-read` of `sbtest1` answers at once (`{"code":200,...,"data":{"k":25124}}`). Nothing that runs
restarts. Adding a slot that is not in config.ini would mean a config change, a management server
reload and a restart of the nodes that must see it. That is why the slots are listed up front, the
same way the Helm chart does with `MySQLdSlotsPerNode` / `EmptyApiSlots`.

## Scale in: not supported for node groups with data

```
$ ndb_mgm -e "DROP NODEGROUP 1"
*  1006: Illegal reply from server
*        error: -2
```

RonDB (like MySQL NDB Cluster) only drops a node group that holds no data: "The management client
is used to add new node groups to the cluster and can also drop node groups that are still empty"
([RonDB management client docs](https://docs.rondb.com/rondb_mgm_client/)). Nothing moves fragments
out of a node group, and `REORGANIZE PARTITION` only spreads data onto new node groups. The
[RonDB Helm chart](https://github.com/logicalclocks/rondb-helm/blob/v26.2.20/templates/topology-immutability.yaml)
says it plainly: "RonDB does not support online add/remove of node groups — take a backup, then
reinstall with the new topology and restoreFromBackup.backupId set to that backup". So going from 4
data nodes back to 2 means backup, a new 2-node cluster and a restore. `ndb_mgm -e "3 deactivate"`
is not scale-in either: it stops node 3 (`Data node to deactivate still up, will stop it`), and node
group 1 keeps serving from node 4 alone with one replica. The target activates and restarts it
again (node restart from its volume, 15 s).

What does scale in online is the API layer: stop `mysqld-2` or `rest` and their slots go back to
`not connected`.

## Scale up: memory and threads via rolling restart

`make scale-up` copies [`config/config-scale-up.ini`](config/config-scale-up.ini) (`DataMemory=1G`,
`NumCPUs=3`) into the management server and restarts it (`Config change completed! New
generation: 6`). Then it gives the data node containers 3 CPUs and restarts the data nodes one by
one with `ndb_mgm -e "N restart"`. Each node takes the new configuration when it restarts, and no
`--initial` is needed:

| per data node | before | after |
|---|---:|---:|
| DataMemory pages (32 KiB) | 24,452 (~764 MiB) | 32,690 (~1,021 MiB) |
| block threads (`NDBMT: number of block threads`) | 2 | 3 (`recv_ldm_tc_main`, `recv_ldm_tc_rep`, `recv_ldm_tc`) |

Under a 120 s `oltp_read_write` load through both MySQL Servers, throughput stayed at 1,540–1,662 tps
in every 5 s interval. Each node took 36–41 s from `restart` to `started`, with small error bursts
(up to 3.2 err/s) while one went down. In total 46 ignored errors in 195,000 transactions.

## Benchmark

`oltp_read_write`, 16 threads, 4 × 50,000 rows, 2026-10-03, Docker Desktop on an Apple M4 Pro (Docker
VM: 11 CPUs, 24.4 GB, aarch64), RonDB 26.02.11. Data nodes and MySQL Servers capped at 2 CPUs each:

| step | data nodes | MySQL Servers | s | tps | qps | avg ms | p99 ms | errors |
|------|-----------:|--------------:|--:|----:|----:|-------:|-------:|-------:|
| before (`baseline`) | 2 | 1 | 30 | 1,077 | 21,548 | 14.85 | 64.47 | 0 |
| during `reorg` | 2 → 4 | 1 | 120 | 620 | 12,406 | 25.80 | 68.05 | 25 |
| after (`after`) | 4 | 1 | 30 | 845 | 16,905 | 18.93 | 65.65 | 0 |
| `add-api` | 4 | 2 | 30 | 1,651 | 33,019 | 9.69 | 15.00 | 0 |
| during `scale-up` (rolling restart) | 4 | 2 | 120 | 1,630 | 32,601 | 9.82 | 17.32 | 46 |

Adding a node group did not raise throughput here. One MySQL Server at 2 CPUs is the bottleneck,
and after the reorganisation every range query scans 8 fragments on 4 nodes instead of 4 on 2:
1,077 → 845 tps. A second MySQL Server nearly doubled it (1,651 tps, p99 64 → 15 ms). Data node
capacity (memory, and CPU once the SQL layer keeps up) is what a node group adds, and on one laptop
VM that does not show. Each figure is from a single short run.

## Known issues

- With a redo log of 4 parts x 4 files x 16 MB, `sysbench prepare` (4 tables loaded in parallel)
  fails with `error 1297 (Got temporary error 410 'REDO log files overloaded (decrease
  TimeBetweenLocalCheckpoints or increase NoOfFragmentLogFiles)' from NDBCLUSTER)`, and sysbench
  still exits 0. The config uses 8 x 64 MB, and [`bench/sb.sh`](bench/sb.sh) checks for `FATAL`.
- `REORGANIZE PARTITION` is online for reads, but writes stall for each table's ALTER (~8 s per
  50,000-row table here, p99 ~7.9 s). Run it table by table, outside peak write load.
- `DROP NODEGROUP` on a node group with data answers `1006: Illegal reply from server` /
  `error: -2`, not a message that says the node group is in use.
- After `REORGANIZE PARTITION` + `OPTIMIZE TABLE`, `DataMemory` used on the old node group did not
  go down (158 → 161 MiB) although half of the rows moved away.
- `ndb_mgmd --initial` rebuilds its config cache from config.ini and forgets `N activate`. If the
  `mgmd` container is recreated (`docker compose up` without `--no-deps` after a compose change),
  nodes 3 + 4 are deactivated again. `make down && make all` starts over.
