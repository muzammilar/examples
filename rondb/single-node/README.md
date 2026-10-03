# RonDB — single node

The smallest RonDB: one management server, one data node, one MySQL Server, as a plain compose
file. No redundancy. For two replicas, failover and the REST API server see
[`../docker-compose-cluster/`](../docker-compose-cluster).

## Quick start

```bash
make up        # start in order (mgmd -> data node -> mysqld), ~30 s; prints memory per container
make test      # sql/*.sql: ENGINE=NDB table, fragments on the one data node (ndbinfo), a transaction
               # + rollback, pk lookup vs table scan (EXPLAIN + NDB API counters); then ndb_desc -pn
make restart   # stop the data node: SQL fails with error 4009; start it: system restart from disk
make benchmark # sysbench OLTP on ENGINE=NDB tables via mysqld (SMOKE=1: smaller)
make status    # ndb_mgm -e show, all report memory
make cli       # interactive mysql client (root) on the MySQL Server
make down      # remove containers, the data node's volume and the built bench image
```

## Setup

| service  | process    | node id | role |
|----------|------------|---------|------|
| `mgmd`   | `ndb_mgmd` | 65      | management server: hands out [`config/config.ini`](config/config.ini) |
| `ndbd`   | `ndbmtd`   | 1       | the only data node: node group 0, `NoOfReplicas=1` |
| `mysqld` | `mysqld`   | 67      | MySQL Server, SQL on top of the NDB tables |

| item | value |
|---|---|
| MySQL | `127.0.0.1:3306`, user `rondb` / `rondb` (port: `RONDB_MYSQL_PORT`); in the container `mysql -uroot` has no password |
| Image | `hopsworks/rondb:26.02.11` (26.02 LTS, amd64 + arm64; override: `RONDB_VERSION`); every service runs as uid 1000 (mysqld will not run as root) |
| Footprint after `make up` (`docker stats`) | `ndbd` ~1.0 GB, `mysqld` ~0.45 GB, `mgmd` ~12 MB |
| Threads | `AutomaticThreadConfig`, `NumCPUs=1`: one block thread for receive, LDM, TC, main and replication (`ndbinfo.threads`: `recv_ldm_tc_main_rep`) |
| Memory | `AutomaticMemoryConfig` stops at `TotalMemoryConfig=2G` (~1.15 GB `DataMemory`, ~1.9 GB process). [`config/config.ini`](config/config.ini) sets pools by hand instead (`AutomaticMemoryConfig=false`, `DataMemory=256M`, `DiskPageBufferMemory=32M`, `SchemaMemory=50M`, ...): log line `Total sum of all pages in Global Memory is 24240, 757 MBytes`, ~1.0 GB process. 256 MB holds the benchmark's 100,000 rows with room to spare; raise `DataMemory` for more data |

- The data node starts with `--initial` only when its volume is empty, so `make restart` recovers
  rows from the local checkpoint + redo log (`Start phase 101 completed (system restart)`).
- mysqld keeps its data dictionary in its container layer; recreate rather than restart it.

## Restart

With one replica the data node holds the only copy of every fragment. While `make restart` has it
stopped, every NDB query fails:

```
ERROR 1296 (HY000) at line 1: Got error 4009 'No data node(s) available, check Cluster state' from NDBCLUSTER
```

On start it does a system restart from its own disk (checkpoint + redo log), mysqld reconnects and
the rows are back. The target took 7.7 s. In the [2-data-node cluster](../docker-compose-cluster)
the same stop is a node failure that SQL does not see.

## Benchmark

[`bench/run.sh`](bench/run.sh) runs sysbench 1.0.20 from Debian ([`bench/Dockerfile`](bench/Dockerfile))
through the MySQL Server: `TABLES` (2) tables of `TABLE_SIZE` (50,000) rows with
`--mysql-storage-engine=ndbcluster`, then `oltp_point_select`, `oltp_read_only` and
`oltp_read_write` for `DURATION` s (30) each with `THREADS` (8) clients.
[`bench/report.py`](bench/report.py) prints the table and writes
`results/rondb-single-<UTC time>.{txt,json}` (gitignored). `sbtest` is dropped at the end.

```bash
make benchmark                    # 2 x 50,000 rows, 30 s per workload, 8 threads (~2.5 min)
make benchmark SMOKE=1            # 2 x 10,000 rows, 10 s per workload
make benchmark THREADS=16 DURATION=60
```

### Sample results

2026-10-03, defaults, Docker Desktop on Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64, native
arm64 image), RonDB 26.02.11, 1 data node (`NumCPUs=1`, `DataMemory=256M`), no CPU limits on the
database containers, bench client `cpus: 2`, other examples running on the same VM.

| workload | tps | qps | avg ms | p50 ms | p99 ms |
|----------|----:|----:|-------:|-------:|-------:|
| oltp_point_select | 28,921 | 28,921 | 0.28 | 0.23 | 0.86 |
| oltp_read_only | 1,809 | 28,942 | 4.42 | 4.25 | 7.70 |
| oltp_read_write | 1,358 | 27,162 | 5.89 | 5.67 | 10.46 |

- No errors.
- `docker stats`: `mysqld` ~380% CPU (uncapped), data node ~84% of its one block thread. `mysqld`
  is the bottleneck.
- With one replica a write commits on one node, no replication round trip.
- Not directly comparable with the [cluster's benchmark](../docker-compose-cluster/README.md#benchmark),
  which caps `mysqld` at 2 CPUs and replicates every write to a second node.

## Known issues

- `TotalMemoryConfig` below 2 GB is refused and `ndb_mgmd` exits:
  ```
  ERROR    -- at line 15: Illegal value 1G for parameter TotalMemoryConfig.
  Legal values are between 2147483648 and 70368744177664
  ```
  Workaround: `AutomaticMemoryConfig=false` with explicit pools (as here).
- Without `AllowUnresolvedHostnames=true` in `[TCP DEFAULT]`, `ndb_mgmd` exits on start because the
  data node's container (and DNS name) does not exist yet:
  `ERROR    -- Could not resolve hostname [node 1]: ndbd`.
