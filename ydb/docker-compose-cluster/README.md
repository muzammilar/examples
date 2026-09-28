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
make benchmark-extended  # scaling, failure under load, tpcc, tpch row vs column (~35 min)
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
row vs column, and per-node load. It waits for a GOOD self-check first. Results go to
`results/ydb-extended-<time>.{log,json}`, with JSON keys `scaling`, `failover`, `tpcc`, `tpch`
and `docker_stats` / `queries_per_dynamic_node` per part. Variables: `EXT_TIME` (per scaling
point), `EXT_THREADS`, `EXT_FO_TIME`, `EXT_FO_THREADS`, `EXT_TPCC_WAREHOUSES`, `EXT_TPCC_TIME`,
`EXT_TPCH_SCALE`, `EXT_TIMEBOX`, `EXT_PARTS`. Sample run 2026-09-28, same machine and budget:
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
