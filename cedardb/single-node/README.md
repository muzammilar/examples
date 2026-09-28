# CedarDB — single node

One [CedarDB](https://cedardb.com/docs/get_started/install_with_docker/) Community Edition
server (`cedardb/cedardb`), the HTAP database from the Umbra team at TUM. It speaks the
PostgreSQL wire protocol, so the stock `psql` client (a `postgres:17-alpine` tools
container, since the image ships only `pg_isready`) drives it.

```bash
make up        # start and wait for pg_isready, print version()
make test      # run sql/*.sql: generate 100k customers + 3M orders with generate_series/random(),
               # join/GROUP BY, percentiles, window functions, EXPLAIN / EXPLAIN ANALYZE,
               # committed + rolled-back transactions and a bulk UPDATE, CSV export + csvview, vector distance
make benchmark # pgbench TPC-B-like + select-only at 1/8/16 clients, then timed analytic queries (SMOKE=1: 10 s runs)
make status    # container state and on-disk size per table
make cli       # interactive psql
make down      # remove the container and its volume
```

- PostgreSQL protocol: `localhost:5434` (host port 5434 to avoid a local PostgreSQL on 5432;
  override with `CEDARDB_PORT=5432 make up`)
- User `postgres`, database `postgres`, fixed demo password `Cedar-Demo-1`
  (`PGPASSWORD=Cedar-Demo-1 psql -h localhost -p 5434 -U postgres`). CedarDB rejects weak
  passwords: 8+ characters with upper, lower, digit and symbol.

The image is multi-arch (amd64 + arm64) and runs natively on Apple silicon. No license key
is needed: without one CedarDB runs as the free Community Edition, limited to 64 GiB of
data (beyond that it turns read-only) and without Enterprise features; see
https://cedardb.com/docs/licensing/. CedarDB is single-node only; there is no cluster
or replication mode to demo. By default it sizes its buffer and work memory to 45% of
the Docker VM's memory each.

## Benchmark

`make benchmark` measures both halves of CedarDB's HTAP pitch on one server
([`bench/run.sh`](bench/run.sh), in the `postgres:17-alpine` image):

| part | how | what it measures |
|---|---|---|
| `tpcb` | `pgbench` built-in TPC-B-like script, `SCALE` x 100k accounts | short read-write transactions: 3 `UPDATE`s, a `SELECT` and an `INSERT` each |
| `select-only` | `pgbench -S` | primary-key point lookups |
| load | [`sql/01-generate.sql`](sql/01-generate.sql) | bulk `INSERT ... SELECT` of 100k customers and 3M orders |
| analytics | [`bench/analytics.sql`](bench/analytics.sql), `RUNS` times | join + `GROUP BY`, TPC-H Q1/Q6-like scans, percentiles, `count(DISTINCT)`, window functions over the 3M orders |

Each pgbench run lasts `DURATION` seconds, once per `CLIENTS` count (`-c N -j N`), with
`--max-tries=MAX_TRIES`. pgbench prints only the average latency, so it logs every transaction
(`-l`) and the script computes p50/p95/p99 from those logs. CedarDB runs at `repeatable read`:
two TPC-B transactions updating the same branch row (there are only `SCALE` of them) do not
wait for each other as on PostgreSQL's default `read committed`, the second one aborts with a
serialization failure and pgbench retries it; the table shows how many transactions needed a
retry and how many still failed after `MAX_TRIES` tries.

What it shows: point lookups and short transactions at PostgreSQL-like latencies, and the same
server scanning, joining and aggregating millions of freshly inserted rows in tens of
milliseconds, without an ETL step into a separate warehouse.

```bash
make benchmark                          # scale 10 (1M accounts), 60 s per run, 1/8/16 clients, 5 analytic runs
make benchmark SMOKE=1                  # scale 2, 10 s per run, 3 analytic runs
make benchmark DURATION=120 CLIENTS="4 32" SCALE=50
```

It prints a summary table (TPS, latency avg/p95/p99, retried/failed transactions per run; load
time; min/median time per analytic query) and keeps the raw pgbench/psql output and a parsed
JSON with the CedarDB and pgbench versions, isolation level, parameters and Docker VM
CPUs/memory in the gitignored `results/cedardb-<UTC time>.{txt,json}`. The parser,
[`bench/report.py`](bench/report.py), is standard-library Python run with `uv run --frozen` in
the `ghcr.io/astral-sh/uv` image. `pgbench -i` warns that CedarDB ignores its `fillfactor`
table option. The benchmark adds the `pgbench_*` tables and regenerates the `customers`/`orders`
tables of `make test`.

Client and server share one Docker VM, so the numbers compare workloads with each other rather
than measure the hardware.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) caps the `cedardb`
container at `BENCH_CPUS=4` / `BENCH_MEM=6g` (no swap) with `docker update`, and restores the
old limits afterwards. Docker cannot remove a memory limit from a running container, so
"unlimited" comes back as the Docker VM's total memory; `make down && make up` starts clean.
The pgbench/psql client has `cpus: 2` in compose (`BENCH_CLIENT_CPUS`). The JSON records the
applied limits under `limits`. CedarDB picks its worker count and buffer size at startup from
the cores and memory it sees, which is the whole VM, since the cap comes later. The cgroup still
caps its CPU time (it peaked at ~4.5 CPUs in `docker stats` samples) and memory (it used
~0.7 GB).

### Sample results

2026-09-28, `make benchmark` (defaults: scale 10, 60 s per run), Docker Desktop 29.5.3 on an
Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64, native arm64 build), CedarDB v2026-09-16
capped at 4 CPUs / 6 GB, client 2 CPUs.

| workload | clients | TPS | avg ms | p95 ms | p99 ms | retried / failed |
|---|---|---|---|---|---|---|
| tpcb | 1 | 1,025 | 0.97 | 1.08 | 1.24 | 0 / 0 |
| tpcb | 8 | 1,449 | 5.47 | 20.23 | 31.37 | 35,212 / 170 |
| tpcb | 16 | 1,474 | 10.04 | 47.54 | 58.73 | 52,058 / 1,895 |
| select-only | 1 | 5,874 | 0.17 | 0.22 | 0.29 | 0 / 0 |
| select-only | 8 | 14,187 | 0.56 | 1.09 | 1.71 | 0 / 0 |
| select-only | 16 | 21,687 | 0.74 | 1.07 | 1.77 | 0 / 0 |

| analytic query (3M orders) | min ms | median ms |
|---|---|---|
| join-group-by | 27.0 | 66.3 |
| q1-like-summary | 17.6 | 18.1 |
| q6-like-filter-sum | 1.1 | 1.1 |
| percentiles | 1,274.0 | 1,278.3 |
| count-distinct | 12.3 | 13.0 |
| window-running-total | 10.0 | 10.7 |
| top-n-per-group | 100.1 | 103.2 |

Load: 3M orders in 4.9 s. CedarDB is an analytics engine first: most queries over 3M rows take
tens of milliseconds, and exact percentiles (~1.3 s) are the one expensive case. Reads scale with
clients, but TPC-B writes do not. At scale 10 every transaction updates one of 10 branch rows, so
under repeatable read 40-60% of transactions hit a serialization conflict and retry, and TPS
flattens at ~1.45k.

### Future work

Add sysbench (`--db-driver=pgsql`) with one common workload set (same scripts, table count/size,
thread counts and duration) shared by every sysbench-capable example — TiDB, OceanBase (single node
and cluster), SingleStore, RonDB, YugabyteDB YSQL and CedarDB — so their numbers compare directly.
This example currently uses its own tool and parameters.
