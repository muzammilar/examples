# RonDB — cluster with Docker Compose

The minimal RonDB (MySQL NDB Cluster fork by Hopsworks) cluster that
[rondb-docker](https://github.com/logicalclocks/rondb-docker) builds, written out as a plain
compose file:

| service  | process                            | node id | role                                                    |
|----------|------------------------------------|---------|---------------------------------------------------------|
| `mgmd`   | `ndb_mgmd`                         | 65      | management server: hands out [`config/config.ini`](config/config.ini), arbitrator |
| `ndbd-1` | `ndbmtd`                           | 1       | data node, node group 0                                 |
| `ndbd-2` | `ndbmtd`                           | 2       | data node, node group 0 (`NoOfReplicas=2`: a replica of every fragment) |
| `mysqld` | `mysqld`                           | 67      | MySQL Server, SQL on top of the NDB tables              |
| `rest`   | `rdrs2`                            | 195     | REST API server: pk-read / batch / scan over HTTP via the NDB API |

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

- MySQL: `localhost:3307`, user `rondb` / `rondb` (override the port with `RONDB_MYSQL_PORT`);
  inside the container `mysql -uroot` has no password.
- REST API: `http://localhost:4406/0.1.0/<db>/<table>/pk-read` (override with `RONDB_REST_PORT`), e.g.
  ```bash
  curl -X POST localhost:4406/0.1.0/demo/accounts/pk-read -H 'Content-Type: application/json' \
    -d '{"filters": [{"column": "id", "value": 3}], "readColumns": [{"column": "balance"}]}'
  ```
  It runs with `Security.InsecureAllowAll` (no API keys, no TLS). At startup it keeps logging
  `[FS Cache Event] Failed to get feature_view table` retries: that is the Hopsworks
  feature-store cache looking for tables this cluster does not have, and harmless.
- Image `hopsworks/rondb:26.02.10` (26.02 LTS, amd64 + arm64; override with `RONDB_VERSION`).
  Every service runs as uid 1000 like rondb-docker (mysqld will not run as root).
- Memory follows rondb-docker's `mini` profile (`TotalMemoryConfig=2100M`, `NumCPUs=2` per data
  node, ~6 GB for the whole cluster). The redo log is shrunk and the disk-data tablespace left out.
- Data nodes start with `--initial` only when their volume is empty, so the restart in
  `make failover` is a real node restart ("Not initial start"): local recovery, then copy the
  changes made meanwhile from node 1. mysqld keeps its data dictionary in its container layer and
  initializes it on first start; recreate rather than restart it.
- `ndb_mgm` and `ndb_desc` run in the `mgmd` container; `ndb_desc`/other NDB API tools use the
  spare `[API]` slots 231–232 in `config.ini`.

## Benchmark

`make benchmark` ([`bench/run.sh`](bench/run.sh)) runs from an image built from
[`bench/Dockerfile`](bench/Dockerfile) (`debian:trixie-slim` + Debian's `sysbench` 1.0.20 and
`wrk` 4.1.0; there is no official sysbench image for arm64):

1. **sysbench OLTP through the MySQL Server.** `sysbench prepare --mysql-storage-engine=ndbcluster`
   creates `TABLES` tables (default 4) of `TABLE_SIZE` rows (50,000) in database `sbtest`, so
   every row lives in the data nodes' memory (`DataMemory`, ~890 MiB per node here) with a
   replica on both nodes. Then `oltp_point_select` (one pk lookup per transaction),
   `oltp_read_only` (10 pk lookups + 4 range queries) and `oltp_read_write` (the same plus 2
   updates, a delete and an insert, committed with two-phase commit across both data nodes), each
   for `DURATION` s (60) with `THREADS` clients (8).
2. **REST API pk-reads.** `wrk` posts `pk-read`s of random `sbtest1` rows
   ([`bench/pk-read.lua`](bench/pk-read.lua)) to `rdrs2` for `DURATION` s over `THREADS`
   connections: the same lookup as `oltp_point_select` without the SQL layer (straight to the NDB
   API).

What it shows: RonDB as an in-memory OLTP store. Point lookups are one round trip to the data
node that owns the key; range queries scan ordered indexes on every fragment; writes pay for
synchronous replication inside the node group before the commit returns.

```bash
make benchmark                        # 4 x 50,000 rows, 60 s per workload, 8 threads (~5 min)
make benchmark SMOKE=1                # 2 x 10,000 rows, 10 s per workload
make benchmark THREADS=32 DURATION=120 TABLE_SIZE=100000
```

It prints a summary table (transactions/s, queries/s, avg/p50/p99 latency, and the errors sysbench
ignored and retried: deadlocks and lock wait timeouts, MySQL errors 1213/1205. In
`oltp_read_write` a transaction that waits on a row lock is aborted after NDB's
`TransactionDeadlockDetectionTimeout` (1.2 s), so a handful of them can lift the average above p99) and keeps the raw sysbench
and wrk output plus parsed JSON with the versions, parameters, cluster layout and Docker VM
CPUs/memory in `results/rondb-<UTC time>.{txt,json}` (gitignored), written by
[`bench/report.py`](bench/report.py) (standard library, `uv run --frozen` in
`ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`). `sysbench cleanup` and `DROP DATABASE
sbtest` run at the end, also when a workload fails. Everything, clients included, shares one
Docker VM with `NumCPUs=2` per data node, so this measures the example, not RonDB on dedicated
machines.

### Sample results

TODO: numbers from a quiet machine.

| workload | tps | qps | avg ms | p50 ms | p99 ms |
|----------|----:|----:|-------:|-------:|-------:|
| oltp_point_select | TODO | TODO | TODO | TODO | TODO |
| oltp_read_only | TODO | TODO | TODO | TODO | TODO |
| oltp_read_write | TODO | TODO | TODO | TODO | TODO |
| rest_pk_read | - | TODO | - | TODO | TODO |
