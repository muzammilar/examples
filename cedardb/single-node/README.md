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

### Sample results

TODO: numbers from a run on a quiet machine (`make benchmark`, defaults).

| workload | clients | TPS | avg ms | p95 ms |
|---|---|---|---|---|
| tpcb | 1 | TODO | TODO | TODO |
| tpcb | 8 | TODO | TODO | TODO |
| tpcb | 16 | TODO | TODO | TODO |
| select-only | 1 | TODO | TODO | TODO |
| select-only | 8 | TODO | TODO | TODO |
| select-only | 16 | TODO | TODO | TODO |

| analytic query (3M orders) | min ms | median ms |
|---|---|---|
| join-group-by | TODO | TODO |
| q1-like-summary | TODO | TODO |
| q6-like-filter-sum | TODO | TODO |
| percentiles | TODO | TODO |
| count-distinct | TODO | TODO |
| window-running-total | TODO | TODO |
| top-n-per-group | TODO | TODO |
