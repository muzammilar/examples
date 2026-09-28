# YugabyteDB — 3-node cluster with Docker Compose

Three `yugabyted` nodes, each in its own simulated zone (`docker.local.zone1..3`): `yb-1`
starts the universe, `yb-2` and `yb-3` join it with `--join=yb-1`, and `make up` runs
`yugabyted configure data_placement --fault_tolerance=zone` so every tablet has one of its
three replicas (RF=3) per zone. Prometheus scrapes each node's `/prometheus-metrics`
(yb-master `:7000`, yb-tserver `:9000`, YCQL `:12000`, YSQL `:13000`) for Grafana.

```bash
make up         # start; nodes join one after another (~30 s), then zone-aware placement
make dashboards # fetch the upstream Grafana dashboard (also run by `make up`; needs curl + jq on the host)
make test       # run ysql/*.sql (sharding, tablet leaders/replicas, transaction, index) and ycql/*.cql (TTL, JSONB, transactional table + index)
make failover   # stop yb-3: YSQL + YCQL keep working after leader re-election; restart it
make benchmark  # ysql_bench TPC-B-like + select-only at 1/8/16 clients over yb-1..3 (SMOKE=1: 10 s runs)
make status     # yugabyted status, yb-admin list_all_masters / list_all_tablet_servers
make cli        # interactive ysqlsh on yb-1
make cli-ycql   # interactive ycqlsh on yb-1
make down       # remove containers (data lives inside them)
```

`make failover` stops `yb-3` and runs [`failover/`](failover) YSQL and YCQL writes and reads on
`yb-1`: with RF=3 the tablets it led elect new leaders on `yb-1`/`yb-2` within seconds, and
`yb-admin` shows its master `TIMED_OUT` and its tserver's heartbeat delay growing. It then starts
`yb-3` again and waits until its master and tserver are `ALIVE`.

- YSQL: `localhost:5433`, YCQL: `localhost:9042` (yb-1)
- yugabyted UI: http://localhost:15433
- yb-master UI: http://localhost:7000, yb-tserver UI: http://localhost:9000 (yb-1)
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (anonymous admin) → **YugabyteDB** dashboard
- `make up` also fetches the official YugabyteDB dashboard from
  [yugabyte-db@v2026.1.2.0/cloud/grafana](https://github.com/yugabyte/yugabyte-db/tree/v2026.1.2.0/cloud/grafana)
  into the gitignored `grafana/provisioning/dashboards/upstream/` → Grafana folder **upstream**.
  `prometheus/prometheus.yml` adds the `node_prefix`/`export_type` labels and the
  `handler_latency_*` → `rpc_latency{saved_name=...}` relabeling it expects. Its YEDIS and RPC queue sizes (Master) panels stay empty.

The image is multi-arch and runs natively on Apple silicon. It runs as uid 10001, so data
stays in each container's writable layer (a fresh named volume would be root-owned).
On macOS, AirPlay Receiver listens on port 7000; turn it off or remap to `7001:7000` if
the port is taken. Tablet leaders start unevenly spread and the load balancer evens them out
over a few minutes. For alerting see
[YugabyteDB Anywhere](https://docs.yugabyte.com/stable/yugabyte-platform/) or the
[Prometheus integration docs](https://docs.yugabyte.com/stable/explore/observability/prometheus-integration/).

## Benchmark

`make benchmark` runs `ysql_bench`, YugabyteDB's fork of `pgbench` that ships in the
`yugabytedb/yugabyte` image ([`bench/run.sh`](bench/run.sh)), against the RF=3 cluster:

| workload | ysql_bench | what it measures |
|---|---|---|
| `tpcb` | built-in TPC-B-like script, `SCALE` x 100k accounts | a distributed read-write transaction: 3 `UPDATE`s on rows in different tablets, a `SELECT` and an `INSERT` |
| `select-only` | `-S` | single-row primary-key reads, answered by the tablet leader |

Each run lasts `DURATION` seconds, once per `CLIENTS` count. The clients are split over one
ysql_bench process per node (16 → 6/5/5 on `yb-1`/`yb-2`/`yb-3`, 1 → `yb-1` only), so every
node's YSQL layer takes connections, and transactions retry up to `MAX_TRIES` times on
conflict/serialization errors. ysql_bench prints only average latencies, so each process logs
every transaction (`-l`) and the script computes p50/p95/p99 over all of them; TPS is the sum
over the processes.

What it shows: the cost of distribution compared with a single-node PostgreSQL-like database
(see the CedarDB example's `make benchmark`, which uses the same pgbench workloads). A point
read needs one RPC from the YSQL layer to the tablet leader; every TPC-B transaction writes
tablets whose leaders sit on different nodes, so it pays for Raft replication to a second
replica on each write plus the distributed commit (transaction status tablet, provisional
records, then applying them). YSQL runs at `read committed` here, so concurrent updates of the
same branch row (there are only `SCALE` of them) wait for each other: with few rows, more
clients mostly add lock waits and latency, not TPS; raise `SCALE` to spread them.

YCQL is not benchmarked: the image ships neither `cassandra-stress` nor YugabyteDB's
`yb-sample-apps` jar (nor a JRE to run it).

```bash
make benchmark                          # scale 10 (1M accounts), 60 s per run, 1/8/16 clients
make benchmark SMOKE=1                  # scale 2, 10 s per run
make benchmark DURATION=120 CLIENTS="3 24 48" SCALE=50
```

It prints a summary table (TPS, latency avg/p95/p99, retried and failed transactions per run)
and keeps the raw ysql_bench output and a parsed JSON with the YugabyteDB/ysql_bench versions,
live tservers, RF, isolation level, parameters and Docker VM CPUs/memory in the gitignored
`results/yugabyte-<UTC time>.{txt,json}`. The parser, [`bench/report.py`](bench/report.py), is
standard-library Python run with `uv run --frozen` in the `ghcr.io/astral-sh/uv` image. The
`ysql_bench_*` tables are recreated on every run.

The three nodes and the clients share one Docker VM, so the numbers compare workloads with each
other rather than measure the hardware.

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
