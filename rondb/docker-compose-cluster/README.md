# RonDB — cluster with Docker Compose

The minimal RonDB (MySQL NDB Cluster fork by Hopsworks) cluster that
[rondb-docker](https://github.com/logicalclocks/rondb-docker) builds, as a plain compose file.

## Quick start

```bash
make up        # start in order (mgmd -> data nodes -> mysqld -> rest), ~40 s
make test      # sql/*.sql: ENGINE=NDB table, fragments and replicas per data node (ndbinfo),
               # a transaction + rollback, pk lookup vs table scan (EXPLAIN + NDB API counters);
               # then ndb_desc -pn and a REST API pk-read with curl
make failover  # stop data node 2: SQL + REST keep working on node 1; restart node 2 (node restart)
make benchmark # sysbench OLTP on ENGINE=NDB tables via mysqld + wrk pk-reads via REST (SMOKE=1: small)
make status    # ndb_mgm -e show, all report memory
make cli       # interactive mysql client (root) on the MySQL Server
make down      # remove containers, the data nodes' volumes and the built bench image
```

## Setup

| service  | process    | node id | role |
|----------|------------|---------|------|
| `mgmd`   | `ndb_mgmd` | 65      | management server: hands out [`config/config.ini`](config/config.ini), arbitrator |
| `ndbd-1` | `ndbmtd`   | 1       | data node, node group 0 |
| `ndbd-2` | `ndbmtd`   | 2       | data node, node group 0 (`NoOfReplicas=2`: a replica of every fragment) |
| `mysqld` | `mysqld`   | 67      | MySQL Server, SQL on top of the NDB tables |
| `rest`   | `rdrs2`    | 195     | REST API server: pk-read / batch / scan over HTTP via the NDB API |

| item | value |
|---|---|
| MySQL | `localhost:3307`, user `rondb` / `rondb` (port: `RONDB_MYSQL_PORT`); in the container `mysql -uroot` has no password |
| REST API | `http://localhost:4406/0.1.0/<db>/<table>/pk-read` (port: `RONDB_REST_PORT`); `Security.InsecureAllowAll` (no API keys, no TLS) |
| Image | `hopsworks/rondb:26.02.10` (26.02 LTS, amd64 + arm64; override: `RONDB_VERSION`); every service runs as uid 1000 like rondb-docker (mysqld will not run as root) |
| Memory | rondb-docker's `mini` profile: `TotalMemoryConfig=2100M`, `NumCPUs=2` per data node, ~6 GB for the cluster; redo log shrunk, no disk-data tablespace |
| NDB tools | `ndb_mgm` and `ndb_desc` run in the `mgmd` container; `ndb_desc` and other NDB API tools use spare `[API]` slots 231–232 |

```bash
curl -X POST localhost:4406/0.1.0/demo/accounts/pk-read -H 'Content-Type: application/json' \
  -d '{"filters": [{"column": "id", "value": 3}], "readColumns": [{"column": "balance"}]}'
```

- Data nodes start with `--initial` only when their volume is empty, so the restart in
  `make failover` is a real node restart ("Not initial start"): local recovery, then copy the
  changes made meanwhile from node 1.
- mysqld initializes its data dictionary on first start and keeps it in its container layer;
  recreate rather than restart it.

## Benchmark

[`bench/run.sh`](bench/run.sh) runs in an image from [`bench/Dockerfile`](bench/Dockerfile)
(`debian:trixie-slim` + Debian's `sysbench` 1.0.20 and `wrk` 4.1.0; there is no official sysbench
image for arm64):

1. **sysbench through the MySQL Server.** `sysbench prepare --mysql-storage-engine=ndbcluster`
   creates `TABLES` (4) tables of `TABLE_SIZE` (50,000) rows in `sbtest`, held in both data nodes'
   `DataMemory` (~890 MiB per node). Then, each for `DURATION` s (60) with `THREADS` (8) clients:
   `oltp_point_select` (one pk lookup per transaction), `oltp_read_only` (10 pk lookups + 4 range
   queries), `oltp_read_write` (the same plus 2 updates, a delete and an insert, two-phase commit
   across both data nodes).
2. **REST pk-reads.** `wrk` posts `pk-read`s of random `sbtest1` rows
   ([`bench/pk-read.lua`](bench/pk-read.lua)) to `rdrs2` for `DURATION` s over `THREADS`
   connections: the same lookup as `oltp_point_select` without the SQL layer.

```bash
make benchmark                        # 4 x 50,000 rows, 60 s per workload, 8 threads (~5 min)
make benchmark SMOKE=1                # 2 x 10,000 rows, 10 s per workload
make benchmark THREADS=32 DURATION=120 TABLE_SIZE=100000
```

- Output: tps, qps, avg/p50/p99 latency and the errors sysbench ignored and retried (deadlocks and
  lock wait timeouts, MySQL 1213/1205). In `oltp_read_write` a transaction waiting on a row lock is
  aborted after `TransactionDeadlockDetectionTimeout` (1.2 s), so a few of them can lift the
  average above p99.
- [`bench/report.py`](bench/report.py) (standard library, `uv run --frozen` in
  `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`) writes raw sysbench/wrk output and JSON
  (versions, parameters, cluster layout, Docker VM CPUs/memory) to
  `results/rondb-<UTC time>.{txt,json}` (gitignored).
- `sysbench cleanup` and `DROP DATABASE sbtest` run at the end, also when a workload fails.
- Everything, clients included, shares one Docker VM: this measures the example, not RonDB on
  dedicated machines.

**Resource budget.** [`bench/limits.sh`](bench/limits.sh) splits `BENCH_CPUS=6` / `BENCH_MEM=12g`
(no swap) with `docker update` for the run:

| container | CPUs | memory | compose `mem_limit` (restored afterwards) |
|---|---:|---:|---:|
| `ndbd-1`, `ndbd-2` (data nodes) | 1.625 each | 4.375 GB each | 2500m |
| `mysqld` (SQL path) | 2 | 2 GB | 1400m |
| `rest` (REST API server) | 0.5 | 1 GB | 512m |
| `mgmd` | 0.25 | 256 MB | 256m |

- Bench client: `cpus: 2` in compose (`BENCH_CLIENT_CPUS`). The JSON records applied limits under
  `limits`.
- Data nodes size themselves from [`config/config.ini`](config/config.ini) (`NumCPUs=2`,
  `TotalMemoryConfig=2100M`), not the cgroup: they used ~2 GB and ~0.5 CPU each.
- With `mysqld` at 1.25 CPUs a first run was mysqld-bound: 11.2k point selects/s vs 19.0k at 2 CPUs.
  At 2 CPUs `mysqld` is still the busiest container (at its cap); `rest` sits at its 0.5 CPU.

### Sample results

2026-09-28, defaults (8 threads, 4 × 50,000 rows, 60 s per workload), Docker Desktop 29.5.3 on
Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64, native arm64 images), RonDB 26.02.10, 2 data
nodes, NoOfReplicas=2, split as above, client 2 CPUs.

| workload | tps | qps | avg ms | p50 ms | p99 ms |
|----------|----:|----:|-------:|-------:|-------:|
| oltp_point_select | 18,957 | 18,957 | 0.42 | 0.22 | 0.47 |
| oltp_read_only | 912 | 14,592 | 8.77 | 4.33 | 57.87 |
| oltp_read_write | 712 | 14,246 | 11.23 | 6.21 | 52.89 |
| rest_pk_read | - | 7,097 | - | 0.53 | 71.27 |

- Point lookups are one round trip to the data node that owns the key: 19k/s through SQL at p99
  0.47 ms.
- Range scans and aggregates in `oltp_read_only` / `read_write` scan ordered indexes on every
  fragment (both data nodes): 4–6 ms p50, ~55 ms tail, from data-node round trips plus `mysqld`
  pinned at 2 CPUs. Writes also wait for synchronous replication inside the node group.
- `rest_pk_read` is capped by the REST server's 0.5 CPU (8 socket errors in 60 s).

### Future work

- Run one common sysbench workload set (same scripts, table count/size, thread counts, duration)
  across every sysbench-capable example: TiDB, OceanBase (single node and cluster), SingleStore and
  RonDB over MySQL; YugabyteDB YSQL and CedarDB with sysbench's `pgsql` driver. Today each example
  uses its own parameters.
- Run TPC-C everywhere (e.g. [go-tpc](https://github.com/pingcap/go-tpc), MySQL and PostgreSQL, or
  [BenchBase](https://github.com/cmu-db/benchbase)) with the same warehouses, threads, duration and
  think-time setting. Warehouse count sets data size and contention; with the spec's keying/think
  times throughput caps at ~12.86 tpmC per warehouse (the YDB example's 10-warehouse run reached
  127 tpmC, i.e. that cap, not its limit), so use enough warehouses or disable think time,
  identically for each database.

## Known issues

- At startup `rdrs2` keeps logging `[FS Cache Event] Failed to get feature_view table` retries:
  the Hopsworks feature-store cache looking for tables this cluster does not have. Harmless.
