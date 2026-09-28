# YDB — 3 storage + 2 database nodes with Docker Compose

A minimal fault-tolerant YDB cluster using [configuration V2](https://ydb.tech/docs/en/devops/deployment-options/manual/initial-deployment/deployment-configuration-v2)
in insecure mode (no TLS, no auth):

- `ydb-storage-{1,2,3}` — static nodes, one per "data center", three sparse
  16 GiB file disks each: the smallest `mirror-3-dc` layout ([`config.yaml`](config.yaml)).
- `ydb-dynamic-{1,2}` — dynamic nodes serving database `/Root/testdb`.
- Prometheus scraping each node's `/counters/counters=<group>/prometheus`, and Grafana.

```bash
make up        # start, `ydb admin cluster bootstrap`, create /Root/testdb, wait for SQL and GOOD
make test      # run sql/*.sql: (re)create table, upsert, select, delete
make benchmark # YDB CLI kv + stock workloads via both dynamic nodes; SMOKE=1 = quick
make benchmark-extended  # scaling, failure under load, tpcc, tpch row vs column, range queries (~75 min)
make benchmark-range     # only the range-query part (PARTS=range); SMOKE=1 = quick
make failover  # stop ydb-storage-3 + ydb-dynamic-2: SQL still works, self-check DEGRADED; restart
make status    # cluster health from the viewer API
make cli       # interactive YQL shell against /Root/testdb
make down      # remove containers (disks live inside them)
```

`make failover` stops one storage node (all of zone-c) and one dynamic node, then runs
[`failover/*.sql`](failover) through `ydb-dynamic-1`: `mirror-3-dc` tolerates one data center down,
so reads and writes keep working while the viewer's `healthcheck` reports `DEGRADED` ("Node is
not available", "Ring has unavailable nodes", later "VDisk is not available"). It then starts both
again and waits until the self-check is `GOOD` and all five nodes are alive (a few minutes emulated).

- gRPC: `grpc://localhost:2136/Root/testdb` (ydb-dynamic-1)
- Embedded UI: http://localhost:8765 (ydb-storage-1)
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (anonymous admin) → **YDB** dashboard
- `make up` also fetches the CPU, DB overview, DB status, Actors, gRPC, Query engine, TxProxy and
  DataShard dashboards from [ydb-platform/ydb@26.2.1.14](https://github.com/ydb-platform/ydb/tree/26.2.1.14/ydb/deploy/helm/ydb-prometheus/dashboards)
  into the gitignored `grafana/provisioning/dashboards/upstream/` → Grafana folder **upstream**.
  They expect the Helm chart's naming, so [`prometheus.yml`](prometheus/prometheus.yml) prefixes
  metric names with their counter group (`utils_`, `kqp_`, …) and sets `container`. Some panels
  stay empty: per-pool panels (User/System/Batch/IC), since with `use_auto_config` on 2 CPUs ydbd
  runs only Common and IO; DB overview's Cluster Health, which needs `ydb_healthcheck` (not
  exported by these counter groups); and some DataShard byte panels.

`ydbd` is taken from the `local-ydb` image (amd64-only, emulated on Apple silicon).

## Benchmark

`make benchmark` runs the YDB CLI's built-in workloads (CLI 2.29 from the image, inside
`ydb-storage-1`) against `/Root/testdb`. The CLI discovers both dynamic nodes and spreads
its sessions over them, so every transaction goes through `ydb-dynamic-1` or
`ydb-dynamic-2`, their DataShards, and `mirror-3-dc` writes to all three storage nodes:

- `workload kv`: `run upsert` (one-row blind writes) and `run select` (point reads by
  primary key) on a `kv_test` table.
- `workload stock`: `run put-rand-order` (inserts a random order and processes it:
  reads its lines and the stock, decrements stock, marks the order processed; serializable
  read-write transactions over the `orders`, `orderLines` and `stock` tables) and
  `run rand-user-hist` (a random customer's orders via the `ix_cust` secondary index).

Each runs for `BENCH_TIME` seconds at each `BENCH_THREADS` count; the table shows the
CLI's own summary: transactions, txs/s, retries (retryable errors, mostly transaction lock conflicts, that the CLI
retried), errors and latency percentiles. Raw CLI output and a JSON summary (versions,
parameters, endpoints, Docker VM CPUs/memory) go to the gitignored `results/`; the
workload tables are dropped with `workload kv|stock clean` afterwards. The last line
gives each dynamic node's share of the queries (from Prometheus' `kqp_Requests_QueryExecute`).

| Variable | Default | `SMOKE=1` |
|---|---|---|
| `BENCH_TIME` | 60 s per run | 10 s |
| `BENCH_THREADS` | `4 16 64` | `8` |
| `BENCH_KV_ROWS` | 10,000 initial rows (keys drawn from 10× that range) | 1,000 |
| `BENCH_PRODUCTS` / `BENCH_ORDERS` | 100 / 10,000 | 100 / 1,000 |
| `BENCH_PARTITIONS` | 4 per table | 4 |

What it shows: throughput and latency of YDB's distributed, serializable transactions
when every write is replicated three ways; `put-rand-order` against `upsert` shows the
cost of multi-shard read-modify-write transactions (and conflicts on hot products) over
single-row writes: with 100 products and 64 threads, retries climb and some orders exceed
the CLI's default 800 ms operation timeout (counted as errors). Under amd64 emulation on Apple silicon the absolute numbers are far
below native; compare runs on the same machine only.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) caps the five `ydbd`
containers at `BENCH_CPUS=6` / `BENCH_MEM=12g` in total with `docker update` (memory without
swap), and restores the old limits afterwards:

| container | CPUs | memory |
|---|---:|---:|
| `ydb-storage-1`-`3` (BlobStorage, one per zone) | 1 each | 2.5 GB each |
| `ydb-dynamic-1`, `-2` (query processing, DataShards) | 1.5 each | 2.25 GB each |

Docker cannot remove a memory limit from a running container, so "unlimited" comes back as the
Docker VM's total memory; `make down && make up` starts clean. Prometheus and Grafana are left
unlimited. There is no separate bench container: the `ydb` CLI load generator runs inside
`ydb-storage-1`, shares its 1 CPU, and keeps that node at its cap (the JSON `limits.note` says
so). A first split with 2 GB per storage node had `ydb-storage-1` killed when the cap was
applied: it had already grown past 2 GB. The actor system is sized by `cpu_count: 2` in
[`config.yaml`](config.yaml), not by the cgroup, and `ydbd` is amd64 under emulation.
`run.sh` records the self-check at the start (`self_check_at_start`) and warns if it is not
GOOD. In two of five cold starts here, a PDisk failed to open (`Can't open file ... errno# 11`
in the storage node's log, `PDisk state is OpenFileError` in the self-check). That left the
cluster DEGRADED, and once `/Root/testdb` unable to answer at all. `make init` now checks for it
and restarts that storage node once, then waits for GOOD.

### Sample results

2026-09-28, `make benchmark` (defaults: 60 s per run), Docker Desktop 29.5.3 on an Apple M4 Pro
(Docker VM: 11 CPUs, 24.4 GB, aarch64; **`ydbd` and the CLI are the amd64 `local-ydb` image under
Rosetta emulation**, so absolute numbers understate native YDB), YDB 26.2.1.14, self-check GOOD
at start, split as above. `0` ms means under 1 ms (the CLI reports whole milliseconds).

| Workload | Threads | txs/s | p50 ms | p95 ms | p99 ms | retries / errors |
|---|---|---|---|---|---|---|
| kv upsert | 4 | 1,443 | 2 | 4 | 5 | 0 / 0 |
| kv upsert | 16 | 4,578 | 3 | 4 | 7 | 0 / 0 |
| kv upsert | 64 | 5,790 | 5 | 48 | 55 | 0 / 64 |
| kv select | 4 | 5,235 | 0 | 0 | 1 | 0 / 0 |
| kv select | 16 | 7,210 | 1 | 1 | 52 | 0 / 0 |
| kv select | 64 | 8,435 | 3 | 61 | 64 | 0 / 0 |
| stock put-rand-order | 4 | 449 | 7 | 14 | 21 | 142 / 0 |
| stock put-rand-order | 16 | 577 | 20 | 59 | 103 | 1,019 / 0 |
| stock put-rand-order | 64 | 527 | 84 | 341 | 691 | 5,532 / 37 |
| stock rand-user-hist | 4 | 3,730 | 0 | 1 | 9 | 0 / 0 |
| stock rand-user-hist | 16 | 5,748 | 1 | 2 | 40 | 0 / 0 |
| stock rand-user-hist | 64 | 6,535 | 5 | 45 | 49 | 0 / 0 |

Queries split 50% / 50% over the two dynamic nodes. Single-row writes, each replicated to three
zones, scale to ~5.8k/s, and point reads to ~8.4k/s. The multi-table serializable order
transaction saturates at ~580/s by 16 threads: with only 100 products, more threads mostly add
lock-conflict retries (5.5k at 64 threads). A few orders then pass the CLI's 800 ms operation
timeout.

### Extended benchmark

`make benchmark-extended` ([`bench/extended.sh`](bench/extended.sh), same budget, ~35 min under
emulation) takes a longer look: thread scaling, a failure test under load, TPC-C and TPC-H
row vs column, range queries ([below](#range-queries)), and per-node load. It waits for a GOOD
self-check first. Results go to `results/ydb-extended-<time>.{log,json}`, with JSON keys
`scaling`, `failover`, `tpcc`, `tpch`, `range` and `docker_stats` / `queries_per_dynamic_node`
per part. Variables: `EXT_TIME` (per scaling point), `EXT_THREADS`, `EXT_FO_TIME`,
`EXT_FO_THREADS`, `EXT_TPCC_WAREHOUSES`, `EXT_TPCC_TIME`, `EXT_TPCH_SCALE`, `EXT_TIMEBOX`, and
`PARTS` (or `EXT_PARTS`) to pick parts, e.g. `make benchmark-extended PARTS="scaling range"`. Sample run 2026-09-28, same machine and budget:
`EXT_TIME=20`, 10 TPC-C warehouses, TPC-H scale 0.1. The TPC-C/TPC-H part was re-run on a fresh
cluster after fixing a table-name clash with `workload stock`.

**Thread scaling** (20 s per point):

| workload | 1 thr | 4 thr | 16 thr | 64 thr |
|---|---|---|---|---|
| kv upsert txs/s (p50 / p99 ms) | 380 (2 / 4) | 1,534 (2 / 5) | 4,494 (3 / 7) | 6,836 (5 / 54) |
| kv select txs/s (p50 / p99 ms) | 1,873 (0 / 1) | 5,355 (0 / 1) | 7,213 (1 / 52) | 7,926 (3 / 63) |
| stock put-rand-order txs/s (p50 / p99 ms) | 164 (5 / 10) | 431 (7 / 24) | 555 (20 / 92) | 563 (85 / 587) |
| put-rand-order retries / errors | 0 / 0 | 38 / 0 | 390 / 0 | 2,076 / 13 |

Writes scale almost linearly to 16 threads and reads to 4. Beyond that the capped CPUs, not
the design, set the ceiling: `ydb-storage-1` (which also runs the CLI) averaged 73% and peaked
at its 1 CPU, and the dynamic nodes averaged ~1 of their 1.5 CPUs. The order transaction
saturates at ~560/s from 16 threads on, and extra threads only turn into conflicts and a long
p99 tail.

**Failure under load**: `kv upsert` and `kv select`, 8 threads each, 180 s, 10 s windows. At
t=31 s `docker compose stop ydb-storage-3` (all of zone-c), at t=74 s `ydb-dynamic-2`, and at
t=90 s both are started again.

| t (s) | 10 | 30 | 40 | 60 | 80 | 90 | 100 | 110 | 120 | 150 | 180 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| event | | | zone-c down | | dyn-2 down | both started | | | | | |
| self-check (nodes) | GOOD (5) | GOOD (5) | DEGRADED (4) | DEGRADED (4) | DEGRADED (3) | | DEGRADED (5) | GOOD (5) | GOOD (5) | GOOD (5) | GOOD (5) |
| upsert txs/s | 1,918 | 1,885 | 2,004 | 2,060 | 2,162 | 2,365 | 2,152 | 1,848 | 1,449 | 1,763 | 1,673 |
| select txs/s | 1,919 | 1,906 | 2,009 | 2,070 | 2,188 | 2,325 | 2,145 | 1,839 | 1,433 | 1,749 | 1,662 |
| errors (upsert + select) | 0 | 0 | 0 | 0 | 11 | 0 | 0 | 0 | 0 | 0 | 0 |
| p99 ms | 24 | 24 | 25 | 24 | 21 | 15 | 10 | 10 | 11 | 9 | 10 |

Losing a whole zone costs nothing visible: `mirror-3-dc` keeps writing to the other two zones,
with no dip, no retries and no errors. Losing a dynamic node fails the 11 in-flight requests on
its sessions (0.002% of 679k), and the CLI carries on through `ydb-dynamic-1`. After the restart
the self-check is GOOD again within ~20 s (t=110 s), with no manual step. Throughput afterwards
is ~20% lower because the client's sessions stay on `ydb-dynamic-1` (it served all queries
counted after the restart; `ydb-dynamic-2`'s counter restarted at 0).

**Workload breadth**: TPC-C and TPC-H.

| TPC-C (`workload tpcc`, 10 warehouses, 90 s, load 42 s) | OK | failed | p50 ms | p90 ms | p99 ms |
|---|---|---|---|---|---|
| NewOrder | 191 | 0 | 79 | 191 | 303 |
| Payment | 173 | 0 | 18 | 92 | 275 |
| Delivery | 16 | 0 | 287 | 402 | 1,090 |
| StockLevel / OrderStatus | 19 / 14 | 0 | 93 / 10 | 169 / 64 | 182 / 67 |

That is 127 tpmC at 100% efficiency. The CLI runs TPC-C with the spec's keying and think times,
so 10 warehouses cap at ~129 tpmC, and YDB kept up with no failed transactions. Finding the
ceiling would take more warehouses than this timebox allows under emulation.

| TPC-H scale 0.1 (600k `lineitem` rows), median of 3 | load | Q1 (scan + aggregate) | Q6 (filtered sum) |
|---|---|---|---|
| row store | 50 s | 0.67 s | 0.13 s |
| column store | 52 s | 0.18 s | 0.21 s |

On the column store Q1, the full-table aggregate, is 3.7x faster. Q6 is a selective filter,
and at this size the row store is about as fast.

**Load distribution**: during scaling, 371,656 vs 377,907 queries on `ydb-dynamic-1` / `-2`
(50/50), with the dynamic nodes averaging 87% / 108% CPU and the storage nodes 73% / 23% / 23%.
`ydb-storage-1` is higher because it also runs the CLI. Memory peaked at 1.0-1.1 GB per dynamic
node and 1.5-2.2 GB per storage node.

**Takeaway.** YDB is at its best where this cluster is built to show it: serializable
distributed transactions, and a zone loss plus a compute-node loss handled automatically, with
~0.002% of in-flight requests failing and self-heal in seconds. The cost is per-transaction
latency. Even a one-row upsert is ~2-3 ms p50 through a dynamic node and three-zone replication,
where the single-node databases in this repo answer in well under a millisecond, and a
multi-table order transaction is 5-20 ms. The engine runs under amd64 emulation on a 6-CPU
budget, so treat the absolute numbers as a floor, and compare shapes, not magnitudes.

### Range queries

`make benchmark-range` (the `range` part of `make benchmark-extended`, same budget) measures
ordered range reads, the access pattern YDB's sorted primary key and global indexes are built for.
The CLI's `workload query` runs fixed query suites and cannot draw random parameters per call,
so the load generator is [`bench/range_bench.py`](bench/range_bench.py) on the official `ydb`
Python SDK (pinned in [`bench/pyproject.toml`](bench/pyproject.toml) / `uv.lock`, run with
`uv run --frozen` in the `bench-range` service, `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`,
capped at `RANGE_CLIENT_CPUS=2` like the bench clients of the other examples):

- Table `range_bench (tenant Uint32, ts Uint64, id Uint64, category Uint32, amount Uint64,
  payload String)`, primary key `(tenant, ts, id)`, pre-split into 4 partitions at tenant
  boundaries. `RANGE_ROWS` rows (1M; `SMOKE=1`: 100k), 20,000 per tenant (so 50 tenants), loaded
  with BulkUpsert (the API behind `ydb import`) from 4 processes, 2,000 rows per request; load
  time and rows/s are reported.
- Then `ALTER TABLE ... ADD INDEX idx_category GLOBAL SYNC ON (category, ts)`, timed until the
  index answers. Built after the load, so the load measures the table alone.
- Workloads, each at `RANGE_THREADS` (`1 4 16 64`) for `RANGE_TIME` seconds (60; `SMOKE=1`: 10)
  after a 2 s warm-up, parameterized YQL with random parameters per call, one serializable
  read-write transaction per query (the default), SDK retries (up to 5) counted:
  - `pk range N`: `WHERE tenant = $t AND ts BETWEEN $a AND $b ORDER BY ts LIMIT N`, N = 10 / 100 / 1,000
  - `index range N`: the same columns via `VIEW idx_category WHERE category = $c AND ts BETWEEN ...`,
    which reads the index shard and then looks each row up in the table
  - `pk agg 10000`: `COUNT(*), SUM(amount)` over a 10,000-row PK range (rows/s counts rows aggregated)
  - full-table streaming scan: one query over all rows, with 1 and 4 parallel streams (rows/s)
- Load: `min(threads, RANGE_PROCS=4)` processes, each with its own driver and
  `QuerySessionPool` and worker threads; processes alternate the seed endpoint between
  `ydb-dynamic-1` and `-2`, and SDK discovery spreads sessions over both (the per-node query
  split from Prometheus is in the JSON). Reported: queries/s, rows/s, p50/p95/p99 ms, retries,
  errors. The table is dropped at the end (and by the cleanup trap if interrupted).

Sample results, 2026-09-28, `make benchmark-range` (defaults: 1M rows, 60 s per point), same
machine and budget as above (`ydbd` amd64 under Rosetta emulation), self-check GOOD at start, no
retries and no errors in any run. Queries/s, with p50 / p99 ms:

| workload | 1 thr | 4 thr | 16 thr | 64 thr |
|---|---|---|---|---|
| pk range 10 | 867 (1.1 / 2.2) | 2,828 (1.3 / 8.6) | 4,124 (2.2 / 44) | 3,741 (8.4 / 68) |
| pk range 100 | 673 (1.4 / 2.8) | 2,319 (1.7 / 2.8) | 2,519 (3.3 / 52) | 2,386 (13 / 79) |
| pk range 1000 | 187 (5.2 / 6.7) | 617 (5.8 / 18) | 571 (15 / 75) | 560 (106 / 210) |
| index range 10 | 445 (2.1 / 3.9) | 1,383 (2.8 / 4.9) | 2,382 (4.6 / 38) | 3,164 (13 / 56) |
| index range 100 | 332 (3.0 / 4.5) | 1,168 (3.3 / 5.4) | 1,643 (6.7 / 41) | 1,725 (27 / 66) |
| index range 1000 | 115 (8.7 / 10) | 347 (11 / 26) | 378 (35 / 71) | 375 (175 / 210) |
| pk agg 10000 | 289 (3.4 / 4.5) | 857 (4.3 / 9.7) | 967 (14 / 49) | 1,030 (65 / 159) |

Load: 1M rows in 5.1 s (197k rows/s over BulkUpsert). Index build: 1.3 s. Full scan: 357k rows/s
with 1 stream, 598k rows/s with 4.

In rows: a 100-row PK range peaks at ~250k rows/s, 1,000-row ranges at ~620k rows/s, and the
aggregate covers 10.3M rows/s at 64 threads, because only one row goes back to the client. A PK
range costs ~1 ms p50 at 10 rows and ~5 ms at 1,000. Going through the global index roughly
doubles latency at low concurrency (index shard read, then a lookup into the table per row). It
costs 40% of throughput at 1,000 rows and 15% at 10 rows / 64 threads. Throughput levels off from
4-16 threads. Both dynamic nodes then average ~1.05-1.1 of their 1.5 CPUs, and the 2-CPU Python
client averages 1.16 CPUs with peaks at its cap. So the 64-thread points and the full scan are
partly client-bound (Python decodes every row), and they are floors. Queries split 47% / 53% over
`ydb-dynamic-1` / `-2`, and the storage nodes stayed under 20% CPU: the data (~130 MB) sits in
the DataShards' caches.

### Future work

- Run one workload set shared with the other examples: sysbench over YDB's PostgreSQL-compatible
  endpoint (once it is enabled here), and one standardized benchmark everywhere, TPC-C
  (`ydb workload tpcc`, go-tpc or BenchBase), with the same warehouses, threads, duration and
  think-time setting as the other databases.
- The 10-warehouse TPC-C result above (127 tpmC) is the spec's think-time cap of ~12.86 tpmC per
  warehouse, not YDB's limit. A comparison needs more warehouses or think time disabled, applied
  equally to every system.
