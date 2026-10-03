# InfluxDB 1.8 cluster (open-source fork) — 3 meta + 2 data nodes

> Third-party software. [`chengshiwen/influxdb-cluster`](https://github.com/chengshiwen/influxdb-cluster)
> is an MIT-licensed fork of InfluxDB 1.8 that re-implements the clustering of the closed-source
> InfluxDB Enterprise 1.x. It is not from InfluxData. It is dormant: the last release, image
> and commit are `v1.8.11-c1.2.0` from 2024-09-08. It is InfluxDB **1.x** (TSM engine, InfluxQL, no
> SQL, no Parquet), not InfluxDB 3.

```bash
make up         # 5 nodes, wait for healthy, join them into one cluster
make test       # scripts/demo.sh (below)
make failover   # stop data-1, write and read through data-2, bring data-1 back, read from it alone
make benchmark  # line protocol ingest + InfluxQL latency, bench/bench.py (SMOKE=1: 200k rows)
make status     # containers and `influxd-ctl show`
make cli        # influx shell on data-1, database fleet
make down       # remove containers and volumes
```

The fork's [Docker quickstart](https://github.com/chengshiwen/influxdb-cluster/tree/master/docker/quick)
with volumes, healthchecks and an automatic join:

- **meta-1..3** (`chengshiwen/influxdb:1.8.11-c1.2.0-meta`): Raft group holding the cluster
  metadata (nodes, databases, shard groups and their owners). HTTP 8091.
- **data-1, data-2** (`...-data`): store TSM shards and serve the 1.x HTTP API. Any data node
  accepts writes and queries for the whole cluster and forwards to the shard owners over TCP 8088.
  Host ports `localhost:8386` and `8387` (`DATA1_PORT`, `DATA2_PORT`).
- **join** one-shot: `influxd-ctl add-meta` ×3 and `add-data` ×2 against meta-1, then
  `influxd-ctl show` (re-running it is harmless).

No auth (the fork supports it as in Enterprise 1.x). Both images are multi-arch and run
natively on arm64.

What `make test` does ([`scripts/demo.sh`](scripts/demo.sh), curl and `influxd-ctl` from the meta
image):

1. `influxd-ctl show`: data nodes 4 and 5, meta nodes 1–3, all `1.8.11-c1.2.0`.
2. `DROP` then `CREATE DATABASE fleet WITH DURATION 30d REPLICATION 2 SHARD DURATION 1d NAME
   month`. With replication 2, every shard is stored on both data nodes.
3. 60 points for 20 hosts: 30 written through data-1 with `consistency=all` (ack once both
   replicas have them), 30 through data-2 with `consistency=quorum`. Both return 204.
4. InfluxQL through either node: `count` = 60 on data-1, `mean(usage), max(load) ... GROUP BY
   region`, `last(usage)` for one host, `SHOW TAG VALUES CARDINALITY` = 20.
5. `influxd-ctl show-shards`: the `fleet` shard has owners `[{ID:4 data-1:8088} {ID:5
   data-2:8088}]`.

What `make failover` showed (2026-10-03, right after a fresh `make up` and `make test`):

```text
==> stopping data-1
write via data-2 consistency=one -> HTTP 204
{"error":"partial write"} <- write via data-2 consistency=all -> HTTP 500
==> reads through data-2 while data-1 is down
cpu,,0,62                                 # the 60 rows + the consistency=one point + the "failed" consistency=all point
cpu,,1791012231000000000,host-failover,0,r9,1.5
cpu,,1791012231000000000,host-failover-all,0,r9,2.5
==> starting data-1, waiting for data-2's hinted-handoff queue to drain
==> stopping data-2: data-1 answers alone
cpu,,0,62
cpu,,1791012231000000000,host-failover,0,r9,1.5
cpu,,1791012231000000000,host-failover-all,0,r9,2.5
==> starting data-2
```

- With a replica down, `consistency=one` succeeds: data-2 stores the point and queues the copy for
  data-1 in its hinted-handoff queue (`/var/lib/influxdb/hh/<node id>`).
- `consistency=all` returns `500 partial write`, but the point is **not rolled back**. It is
  stored on data-2 and queued for data-1 like the other one. A client that retries on 500 writes
  it twice (an idempotent overwrite here, since the timestamp and series are the same).
- Reads stay complete with one data node down (RF 2). After data-1 restarts, the queue drains
  into it. With data-2 then stopped, data-1 alone returns all 62 rows, including both points
  written while it was down.
- Running `make test` again after a failover (it drops and recreates `fleet`) twice gave a
  **diverged replica**: data-1 counted 60 rows, data-2 122 (and 62 against 124 the first time).
  The recreated database got the dropped database's shard id (1), and data-2, restarted during
  the failover, still had the old shard's data under that id. A third try got a new shard id (4)
  and both nodes counted 60. `make down && make up` starts clean.

## Benchmark

`make benchmark` runs [`bench/bench.py`](bench/bench.py) (standard library, `uv run --frozen` in
`ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`). It recreates database `bench`
(`REPLICATION 2`, 1-day shards). It posts `ROWS` (2,000,000) rows of
`cpu,host=host-N,region=rM usage_user=..,usage_system=..,usage_idle=..,load=..i` for `SERIES`
(10,000) hosts, one point per host every 10 s ending now. The data is pre-built in requests of
`BATCH` (10,000) lines, and `WRITERS` (16) threads post them to `/write?consistency=CONSISTENCY`
(`one`), alternating between the two data nodes. Then it polls `count()` through each data node
until both replicas hold every row. Each InfluxQL query then runs `ITER` (30) times through
data-1. The result goes to `results/influxdb1-cluster-<UTC time>.json`.

**Resource budget** ([`bench/limits.sh`](bench/limits.sh), `docker update` for the run, restored
afterwards): 4 CPUs / 6 GB in total. Each meta node gets 0.33 CPU / 512 MB, and the two data
nodes 1.5 CPUs / 2.25 GB each. The bench client has 2 CPUs.

### Sample results

2026-10-03, Docker Desktop on an Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GiB, aarch64, native
images), influxdb-cluster 1.8.11-c1.2.0, the limits above, 2M rows / 10k series, RF 2:

| writers | consistency | rows/s | request p50 | request p99 | replicas complete after last ack |
|--------:|-------------|-------:|------------:|------------:|------:|
| 16 | `one` (default) | 202,113 | 701 ms | 1,501 ms | 1.3 s (data-1 at 1,997,916 rows on the last ack) |
| 16 | `all` | 201,115 | 734 ms | 1,454 ms | 0.4 s (both at 2,000,000 on the last ack) |
| 4 | `one` | 251,811 | 107 ms | 603 ms | 1.8 s (data-2 at 1,937,334) |

Queries through data-1, default run (all runs within ~10%), p50 / p99 ms:

| InfluxQL | rows | p50 | p99 |
|----------|-----:|----:|----:|
| `SELECT count(usage_user) FROM cpu` (2M points) | 1 | 310 | 399 |
| `mean(usage_user) ... WHERE time > now() - 5m GROUP BY region` | 16 | 96 | 103 |
| one host, all 200 points | 200 | 0.44 | 67 |
| `last(usage_user)` for one host | 1 | 0.14 | 0.27 |
| `last(usage_user) ... GROUP BY host` (10k hosts) | 10,000 | 172 | 189 |
| `SHOW TAG VALUES ... WITH KEY = host` | 10,000 | 12.9 | 16.4 |

- Each row is stored twice (RF 2), so ~200k rows/s means ~400k point-writes/s across two data
  nodes with 1.5 CPUs each. `consistency=all` cost no throughput here, since both nodes were
  healthy and the writes were batched. With `one`, the second replica trailed the acks by up to
  60k rows and caught up 1.3–1.8 s after the last one.
- Fewer, smaller concurrent requests did better (4 writers: 252k rows/s, p50 107 ms) than 16
  writers queueing on 3 CPUs.
- The TSM index makes single-series lookups very fast (`last()` for one host 0.14 ms, one host's
  200 points 0.44 ms). Full scans (`count` over 2M points) and 10k-series `GROUP BY host` cost
  100s of ms. Compare [`../v3-core-single-node`](../v3-core-single-node/README.md#benchmark),
  where InfluxDB 3 needs a last value cache to get a single-series lookup under 1 ms.
