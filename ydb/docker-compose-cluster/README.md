# YDB — 3 storage + 2 database nodes with Docker Compose

A minimal fault-tolerant YDB cluster using [configuration V2](https://ydb.tech/docs/en/devops/deployment-options/manual/initial-deployment/deployment-configuration-v2)
in insecure mode (no TLS, no auth):

- `ydb-storage-{1,2,3}` — static nodes, one per "data center", three sparse
  16 GiB file disks each: the smallest `mirror-3-dc` layout ([`config.yaml`](config.yaml)).
- `ydb-dynamic-{1,2}` — dynamic nodes serving database `/Root/testdb`.
- Prometheus scraping each node's `/counters/counters=<group>/prometheus`, and Grafana.

```bash
make up        # start, `ydb admin cluster bootstrap`, create /Root/testdb, wait for SQL
make test      # run sql/*.sql: (re)create table, upsert, select, delete
make benchmark # YDB CLI kv + stock workloads via both dynamic nodes; SMOKE=1 = quick
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

### Sample results

TODO: numbers from a quiet machine.

| Workload | Threads | txs/s | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|
| kv upsert | | | | | |
| kv select | | | | | |
| stock put-rand-order | | | | | |
| stock rand-user-hist | | | | | |
