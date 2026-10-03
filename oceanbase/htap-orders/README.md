# OceanBase — HTAP orders (Go)

One OceanBase CE observer and a Go program that runs OLTP and analytics on the same hybrid
row/column table at the same time.

## Quick start

```bash
make up      # start the observer, wait for "boot success!" (~1 min), build the app
make run     # the five phases below, ~6 minutes; output also in results/
make status  # servers, tenants
make cli     # obclient as root@test in database htap
make down    # remove the container, its data and the app image
```

The observer runs `MODE=mini` as in [`../single-node`](../single-node), with tenant parameters
from obd's `htap` scenario. The Go program ([`app/main.go`](app/main.go)) uses
`go-sql-driver/mysql` over the plain MySQL protocol and runs transactions and analytics **on the
same table at the same time**.

The table `orders` (10 columns, `HASH(id)` 8 partitions) is a hybrid row/column table:

```sql
CREATE TABLE orders (...) PARTITION BY HASH(id) PARTITIONS 8
  WITH COLUMN GROUP (all columns, each column);  -- row copy + one column group per column (4.3+)
```

Writes go to one table and the engine keeps both layouts. A query picks the row copy or the column
groups by cost. The program forces each one with the `NO_USE_COLUMN_TABLE(orders)` /
`USE_COLUMN_TABLE(orders)` hints, so the comparison runs on the same data.

`make run` phases:

1. Loads `ROWS` (2,000,000) orders over 30 days with 8 workers, then runs a tablet-level
   `ALTER SYSTEM MAJOR FREEZE TABLET_ID = ...` on the 8 `orders` tablets. Compaction writes the
   column groups. A tenant-wide major freeze also rewrites ~800 system tablets and took ~6 minutes.
2. Analytics alone: revenue by region, top 10 products, last-10-minutes by status. Each runs
   through the row store, the column store, and the column store with `PARALLEL(4)` (median of 3). The program prints the
   `EXPLAIN` operator to show the hint took effect: `TABLE FULL SCAN` vs `COLUMN TABLE FULL SCAN`.
3. OLTP alone: 16 workers, each transaction inserts an order, advances the status of one of the
   last 10,000 orders, and reads it back by primary key (after a warm-up run of the same length).
4. OLTP plus 2 analytics workers looping the three queries through the **row** store.
5. The same through the **column** store. Halfway through, a freshness probe commits 500 orders in
   a new region and runs one column-store aggregate right away.

Knobs (environment for `make run`): `ROWS`, `DURATION` (30 s per phase), `OLTP_WORKERS` (16),
`OLAP_WORKERS` (2), `DOP` (4), `REPEATS` (3). MySQL protocol is on `localhost:2891`
(`OB_PORT`), `mysql -h127.0.0.1 -P2891 -uroot@test`.

## Sample output

2026-10-02, fresh `make up` then `make run` (defaults), Docker Desktop on an Apple M4 Pro (Docker
VM: 11 CPUs, 24.4 GB, aarch64, native arm64 image), OceanBase CE 4.4.2.1 capped at 6 CPUs, the app
at 2 CPUs. Other containers were running on the same Docker VM, so absolute numbers move ±30%
between runs, but the row/column ratios held.

```
loaded 2000000 rows in 46.2s (43247 rows/s, 8 workers x 1000-row INSERTs)
major compaction of 8 tablets (row copy + column groups) done in 170.3s, 144.0 MB on disk

== 2. analytics alone (median of 3 runs) ==
plan with /*+ NO_USE_COLUMN_TABLE(orders)  */ -> TABLE FULL SCAN
plan with /*+ USE_COLUMN_TABLE(orders)     */ -> COLUMN TABLE FULL SCAN
query                                 row store           column store   column + PARALLEL(4)   row/col
revenue by region                         608ms                  123ms                   96ms      4.9x
top 10 products                           358ms                   55ms                   22ms      6.5x
last 10 min by status                    1007ms                    6ms                   10ms    170.2x

== 5. OLTP + analytics through the COLUMN store ==
  freshness: committed 500 orders in new region "probe6631"; the next column-store query (started at commit, took 100 ms) counted 500 of them, sum 158970.36
  freshness: column-store COUNT(*) = 2136498, including 136498 orders inserted after the compaction (row-format memtable / minor SSTables, merged at read time)

== summary ==
phase                               OLTP tps    p50 ms    p99 ms  vs alone   OLAP q/min   OLAP p50 s
OLTP alone                              1486      8.84     44.72      100%            -            -
OLTP + row-store analytics               961     13.38     69.00       65%          260         0.41
OLTP + column-store analytics           1104     12.36     48.90       74%          939         0.11
```

No errors in any phase.

A rerun on 2026-10-03 (fresh `make up`, defaults, no other containers on the Docker VM) also had
no errors and kept the same ordering, with different spreads: load 37.8 s, compaction 168 s;
row/column 215/101 ms, 233/34 ms and 237/4 ms; OLTP alone 1,732 tps; with row-store analytics
498 tps (29%, 408 q/min), with column-store analytics 636 tps (37%, 1,180 q/min). OLTP lost more
throughput to the analytics workers in that run, but the column store still finished about 2.9x
more queries than the row store, and OLTP kept more of its throughput with column-store analytics.

## Why OceanBase does well here

- **One engine, one copy of the truth.** The same `orders` table takes 1,000+ write
  transactions/s and answers full-table aggregations. There is no CDC pipeline, no second
  database and no ETL lag. The freshness probe's 500 orders were in the very next column-store
  aggregate, because new writes sit in the row-format memtable and are merged into column scans at
  read time until the next compaction folds them into the column groups.
- **Column groups pay off for analytics.** Aggregates read only the columns they need, not the
  two wide address/note columns. They also use per-block min/max (skip index) to prune, which is
  why the time-filtered query drops from about 1 s to a few ms. With `PARALLEL(4)` the same query
  fans out over the 8 partitions.
- **Analytics hurt OLTP less on columns.** With the same 2 analytics workers running back to back,
  the column store finished 3.6x more queries per minute at a quarter of the latency, and OLTP kept
  74% of its solo throughput, against 65% with row-store scans (37% against 29% in the 2026-10-03
  rerun).
- **MySQL compatible.** It is the stock Go MySQL driver and ordinary SQL: partitions, hints,
  `EXPLAIN` and transactions.

Limits of this demo: one observer, so no replicas. OceanBase 4.3.3+ can also put the column
store on a separate read-only *columnstore replica* (`C` in the locality) in its own zone, to
isolate analytics physically. That needs a multi-zone cluster like
[`../docker-compose-cluster`](../docker-compose-cluster). Tenant-level isolation (a second
tenant with its own CPU/memory unit for analytics) does not fit in the `mini` observer's 6G
`memory_limit` (`sys` 2G, `test` 2G + its meta tenant 1G).

## Notes

- `make up` sets `memstore_limit_percentage = 50` (cluster-wide, as `root@sys`). `mini` sizes the
  memstore at about 25% of the 2G tenant, which puts the freeze trigger above the write-throttling
  threshold. A 2M-row load then stalled at ~200k rows instead of finishing in under a minute.
- Run against a fresh observer for comparable numbers (`make down up run`). On a second `make run` in the
  same container the load took 450 s instead of 46 s: the `test` tenant's 1.5G log disk (`mini`
  default) is still ~78% full from the first run, and writes are throttled until checkpoints
  catch up.
- obd checks host limits before deploying. On a Docker VM shared with ScyllaDB, `fs.aio-max-nr`
  (65536) was nearly used up and obd refused to start (`OBD-1011: Insufficient AIO`). The fix is to
  raise it in the Docker VM (`docker run --rm --privileged alpine sysctl -w fs.aio-max-nr=1048576`;
  this resets when Docker restarts). obd also wants ~10G of free disk in the Docker VM, for the
  preallocated 2G data file and 4G log disk.
