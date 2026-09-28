# TiDB — minimal cluster with Docker Compose

A real (if small) TiDB cluster from the multi-arch `pingcap/pd`, `pingcap/tikv` and
`pingcap/tidb` images, all pinned to `v8.5.8`: 3 PD (placement driver: metadata, the
TSO timestamp oracle, region scheduling), 3 TiKV stores (data in Raft-replicated regions,
3 replicas each) and 1 stateless TiDB SQL node. A small `mysql` container (MariaDB's CLI)
runs the SQL. `make up` waits until all three stores are Up and every region has its
three replicas.

```bash
make up        # start PD -> TiKV -> TiDB (~30 s), wait for 3 stores Up and full replication
make test      # run sql/*.sql (cluster_info, AUTO_RANDOM, SPLIT TABLE / SHOW TABLE REGIONS, EXPLAIN ANALYZE cop tasks, transactions), then conflict/
make conflict  # two concurrent sessions: optimistic write conflict at COMMIT, pessimistic lock wait timeout
make benchmark # go-tpc TPC-C (tpmC) + sysbench oltp_point_select / oltp_read_write through TiDB
make status    # cluster_info, tikv_store_status, pd-ctl member leader / store
make cli       # interactive mysql client on TiDB
make down      # remove containers, volumes and the locally built client/bench images
```

The cluster needs ~8 GB of Docker memory (each TiKV settles at ~2.3 GB RSS).

- TiDB (MySQL protocol): `localhost:14000`, user `root`, no password (`TIDB_PORT` overrides)
- PD API: http://localhost:12379/pd/api/v1/stores (`PD_PORT` overrides)
- other versions: `TIDB_VERSION=v8.5.7 make up`

`sql/` in order:

| file | shows |
| --- | --- |
| `01-cluster.sql` | `TIDB_VERSION()`, `information_schema.cluster_info`, `tikv_store_status` |
| `02-schema-data.sql` | tables with `AUTO_RANDOM` keys, 500 customers and 20000 orders generated in SQL |
| `03-auto-random.sql` | the shard bits in generated ids and `LAST_INSERT_ID()` |
| `04-regions.sql` | `SPLIT TABLE ... REGIONS 8` with `tidb_scatter_region`, `SHOW TABLE ... REGIONS`, peers and leaders per store from `tikv_region_status` / `tikv_region_peers` |
| `05-explain.sql` | `EXPLAIN ANALYZE`: `cop[tikv]` operators pushed down to TiKV, `cop_task: {num: 8 ...}` one task per region |
| `06-transactions.sql` | pessimistic (`FOR UPDATE`) and optimistic transfers, a cross-region transaction, rollback |

PingCAP's quick start recommends [TiUP playground](https://docs.pingcap.com/tidb/stable/quick-start-with-tidb/)
for a local cluster, and its old Docker Compose deployment
([pingcap/tidb-docker-compose](https://github.com/pingcap/tidb-docker-compose)) is no longer
maintained. This example uses Docker Compose anyway so that nothing but Docker is needed and
every component is a pinned image.

Notes:
- `config/tikv.toml` shrinks TiKV for a laptop: 256 MB block cache per store (the default is
  45% of the Docker VM's memory, per store), no 5 GB reserved disk, and a 2 GB reported
  capacity. Without the last one, a Docker disk that is more than 80% full makes PD treat
  every store as low-space and it stops placing replicas.
- AUTO_RANDOM shard bits come from the transaction start timestamp, so all rows of one
  statement share a shard; `02-schema-data.sql` loads in 8 statements to spread them.
- TiKV logs warnings about kernel parameters (`somaxconn`, `swappiness`) and the
  `overlay` filesystem; they are harmless here.
- TiFlash (columnar replicas) is left out; it needs its own config and ~1 GB more memory.
  Prometheus/Grafana are left out too: every component serves Prometheus metrics
  (`pd:2379/metrics`, `tikv:20180/metrics`, `tidb:10080/metrics`) on the compose network
  if you want to add them.

## Benchmark

`make benchmark` ([`bench/run.sh`](bench/run.sh)) runs from an image built from
[`bench/Dockerfile`](bench/Dockerfile): PingCAP's [go-tpc](https://github.com/pingcap/go-tpc)
v1.0.12, built from source in `golang:1.26-alpine` (there is no arm64 image), and Debian's
`sysbench` 1.0.20 on `debian:trixie-slim`. Both connect to TiDB's MySQL port (`tidb:4000`) on the
compose network:

1. **TPC-C (go-tpc).** `prepare` loads `WAREHOUSES` warehouses (default 4, ~100 MB each before
   replication) into database `tpcc`, then `run` drives the TPC-C mix (45% new-order, 43% payment,
   4% each order-status, delivery, stock-level; no keying/think time) for `DURATION` s (60) with
   `THREADS` connections (8). The headline is **tpmC**: new-order transactions per minute. Without
   think time it is far above the TPC-C limit of 12.86 tpmC per warehouse, so it is a throughput
   test, not a compliant TPC-C result.
2. **sysbench OLTP.** `TABLES` tables (4) of `TABLE_SIZE` rows (50,000) in database `sbtest`, then
   `oltp_point_select` (one pk lookup per transaction) and `oltp_read_write` (10 pk lookups, 4 range
   queries, 2 updates, a delete and an insert), each for `DURATION` s with `THREADS` threads.

What it shows: distributed SQL over three TiKV stores. Every statement is planned by the stateless
TiDB node and executed as coprocessor/KV requests against the region leaders spread over the three
stores; every commit is a Percolator two-phase commit (a timestamp from PD, prewrite + commit
through Raft to 3 replicas), which is what dominates the write-heavy TPC-C transactions.

```bash
make benchmark                        # 4 warehouses, 4 x 50,000 rows, 60 s per workload, 8 threads (~8 min)
make benchmark SMOKE=1                # 1 warehouse, 2 x 10,000 rows, 10 s per workload
make benchmark WAREHOUSES=10 THREADS=32 DURATION=300
```

It prints the tpmC and a per-transaction-type table (count, tpm, avg/p50/p95/p99 latency, failed
transactions), a sysbench table (transactions/s, queries/s, avg/p50/p99 latency, errors sysbench
ignored and retried: TiDB write conflicts 8002/8022/9007, deadlocks 1213 and lock wait timeouts
1205) and keeps the raw output plus parsed JSON with the versions, parameters, cluster layout and
Docker VM CPUs/memory in `results/tidb-<UTC time>.{txt,json}` (gitignored), written by
[`bench/report.py`](bench/report.py) (standard library, `uv run --frozen` in
`ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`). `DROP DATABASE tpcc, sbtest` runs at the
end, also when a workload fails. Set `GOPROXY` to build go-tpc through a Go module proxy of your
own. Everything (3 PD, 3 TiKV, TiDB and the clients) shares one Docker VM, so this measures the
example, not TiDB on dedicated machines.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) caps the cluster with
`docker update` (memory without swap), at `BENCH_CPUS=6` / **`BENCH_MEM=16g`**:

| container | CPUs | memory |
|---|---:|---:|
| `tikv0`-`tikv2` | 1.25 each | 4.17 GB each |
| `tidb` | 1.5 | 2 GB |
| `pd0`-`pd2` | 0.25 each | 512 MB each |

**Memory is 16 GB, not the 12 GB used for the other clusters.** TiKV sizes its
`memory-usage-limit` and write buffers from the machine at startup (the Docker VM here, since
the cap is applied later), and at 2.8 GB per TiKV two of the three stores were OOM-killed during the
TPC-C load. At ~4.2 GB each they peaked at 2.8-3.5 GB. The JSON records this, with the applied
limits, under `limits`. The old limits come back afterwards. Docker cannot remove a memory limit
from a running container, so "unlimited" returns as the Docker VM's total memory;
`make down && make up` starts clean. The bench client has `cpus: 2` in compose
(`BENCH_CLIENT_CPUS`). TiKV and TiDB also size their thread pools from the VM's 11 cores at
startup, and the cgroup caps what they get.

### Sample results

2026-09-28, `make benchmark` (defaults: TPC-C 4 warehouses, 8 threads, 60 s; sysbench
4 × 50,000 rows), Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64,
native arm64 images), TiDB/TiKV/PD v8.5.8, split as above, client 2 CPUs.

| TPC-C | tpmC | tpmTotal | new-order p99 ms |
|-------|-----:|---------:|-----------------:|
| 4 warehouses, 8 threads | 10,092 | 22,686 | 71.3 |

| workload | tps | qps | avg ms | p50 ms | p99 ms |
|----------|----:|----:|-------:|-------:|-------:|
| oltp_point_select | 17,375 | 17,375 | 0.46 | 0.31 | 0.89 |
| oltp_read_write | 241 | 4,825 | 33.16 | 14.99 | 80.03 |

Point selects are cheap (17k/s, p99 0.9 ms), since TiDB goes straight to the region leader.
Each `oltp_read_write` transaction pays a Percolator two-phase commit over Raft on 3 TiKVs, so it
runs ~70x slower than a point select. TPC-C finished with no errors at ~10k tpmC on 3.75 CPUs of
TiKV.
