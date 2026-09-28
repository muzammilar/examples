# YugabyteDB — 3-node cluster with Docker Compose

Three `yugabyted` nodes, each in its own simulated zone (`docker.local.zone1..3`): `yb-1`
starts the universe, `yb-2` and `yb-3` join it with `--join=yb-1`, and `make up` runs
`yugabyted configure data_placement --fault_tolerance=zone` so every tablet has one of its
three replicas (RF=3) per zone. Prometheus scrapes each node's `/prometheus-metrics`
(yb-master `:7000`, yb-tserver `:9000`, YCQL `:12000`, YSQL `:13000`) for Grafana.

```bash
make up         # start; nodes join one after another (~30 s), then zone-aware placement
make dashboards # fetch the upstream Grafana dashboard (also run by `make up`; needs curl + jq on the host)
make test       # run ysql/*.sql (sharding, tablet leaders/replicas, transaction, index) and ycql/ (see below)
make failover   # stop yb-3: YSQL + YCQL keep working after leader re-election; restart it
make benchmark  # ysql_bench TPC-B-like + select-only at 1/8/16 clients over yb-1..3 (SMOKE=1: 10 s runs)
make benchmark-ycql # yb-sample-apps key-value writes, reads, 50/50 at 1/8/32 threads over YCQL (SMOKE=1: 10 s runs)
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

## YCQL in `make test`

YCQL is YugabyteDB's Cassandra-compatible API on the same DocDB storage as YSQL, so unlike
Cassandra it is strongly consistent: every write goes through the tablet's Raft group (RF=3 here)
and reads go to the tablet leader. A keyspace takes its replication from the universe (no
`replication` map needed), and the extensions below have no Cassandra equivalent:

| file | shows |
|---|---|
| [`ycql/test.cql`](ycql/test.cql) | JSONB column, per-row TTL, a transactional table with a secondary index, `BEGIN TRANSACTION`, `partition_hash` |
| [`ycql/schema/indexes.cql`](ycql/schema/indexes.cql) | the tables and indexes for the next two files, created first (see below) |
| [`ycql/indexes.cql`](ycql/indexes.cql) | a covering index (`INCLUDE`): `EXPLAIN` shows *Index Only Scan* for covered columns and *Index Scan* otherwise; a `UNIQUE` index (*Index Only Key Lookup*) |
| [`ycql/errors/unique-violation.cql`](ycql/errors/unique-violation.cql) | a duplicate email, rejected by the unique index (`make test` expects the error named in its `-- expect:` line) |
| [`ycql/transactions.cql`](ycql/transactions.cql) | one distributed transaction over two tables; `writetime()` shows the single commit time on both rows |
| [`ycql/jsonb.cql`](ycql/jsonb.cql) | `->` / `->>` on nested documents and arrays, an index on a JSONB attribute, filters with `CAST`, partial `UPDATE`s of single attributes |
| [`ycql/ttl/`](ycql/ttl) | table default TTL, `USING TTL` per row and per column; `make test` reads again 6 s later: the row with the table default is gone, the column TTL cleared one column |
| [`ycql/partitioning.cql`](ycql/partitioning.cql) | `partition_hash()` (0–65535) and `token()` per row, a table `WITH tablets = 4` and its hash ranges in `system.partitions`, a parallel scan split by hash and by token range |

Secondary indexes need `transactions = {'enabled': true}` on the table (the index is updated in
the same distributed transaction as the row), and a table with a secondary index cannot take
row-level TTLs, which is why the TTL demo uses its own table. `make test` creates the indexed
tables in `ycql/schema/` and waits 3 s before using them: a CQL proxy (here yb-1's) that touches a
table while its new index is still backfilling caches the index as not yet readable and keeps
planning `Seq Scan` for it (writes still maintain the index; other nodes' proxies use it).
JSONB updates must end the path in `->` (`SET doc->'name' = '"desk lamp"'`); the value is JSON.

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

YCQL has its own target, `make benchmark-ycql` (below).

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

**Resource budget.** For each run (`make benchmark` and `make benchmark-ycql`) [`bench/limits.sh`](bench/limits.sh) gives the three
`yugabyted` nodes `BENCH_CPUS=6` / `BENCH_MEM=12g` in total, i.e. 2 CPUs / 4 GB each (no swap),
with `docker update`, and restores the old limits afterwards. Docker cannot remove a memory
limit from a running container, so "unlimited" comes back as the Docker VM's total memory;
`make down && make up` starts clean. Prometheus and Grafana are left unlimited. The
`ysql_bench` and yb-sample-apps clients have `cpus: 2` in compose (`BENCH_CLIENT_CPUS`). The JSON records the
applied limits under `limits`. yb-master and yb-tserver size their memory trackers and thread
pools from the RAM and cores they see at startup, which is the whole VM, since the cap is applied
to running containers. The cgroup caps what they actually get: all three nodes sat at their
2 CPUs during the run and used ~1 to 1.2 GB.

### Sample results

2026-09-28, `make benchmark` (defaults: scale 10, 60 s per run), Docker Desktop 29.5.3 on an
Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64, native arm64 image), YugabyteDB
2026.1.2.0, 3 nodes RF=3, read committed, 2 CPUs / 4 GB per node, client 2 CPUs.

| workload | clients | TPS | avg ms | p95 ms | p99 ms |
|---|---|---|---|---|---|
| tpcb | 1 | 275 | 3.64 | 4.49 | 5.37 |
| tpcb | 8 | 692 | 11.55 | 47.63 | 60.66 |
| tpcb | 16 | 733 | 21.81 | 75.55 | 97.89 |
| select-only | 1 | 3,640 | 0.27 | 0.35 | 0.43 |
| select-only | 8 | 13,964 | 0.57 | 0.51 | 0.83 |
| select-only | 16 | 19,780 | 0.81 | 0.73 | 2.19 |

No transaction failed or needed a retry. Single-row reads are served by the tablet leader and
scale to ~20k TPS. A TPC-B transaction writes 4 rows across tablets and commits through Raft
on 3 nodes. That makes it ~13x slower than a read with 1 client (3.6 ms vs 0.27 ms), and with
the nodes CPU-bound it stops scaling at ~730 TPS.

## YCQL benchmark

`make benchmark-ycql` runs [yb-sample-apps](https://github.com/yugabyte/yb-sample-apps)
v1.4.3 — the workload generator YugabyteDB's own
[YCQL key-value benchmark](https://docs.yugabyte.com/stable/benchmark/key-value-workload-ycql/)
uses — with its `CassandraKeyValue` workload ([`bench/run-ycql.sh`](bench/run-ycql.sh)). The jar
is not in any image: the Makefile downloads it once into the gitignored
`bench/yb-sample-apps.jar` and checks its sha256; it runs on the multi-arch
`eclipse-temurin:17.0.20_8-jre-noble` image (arm64-native on Apple silicon). It drops
`ybdemo_keyspace.cassandrakeyvalue` (`k varchar PRIMARY KEY, v blob`) first, then:

| workload | yb-sample-apps | what it measures |
|---|---|---|
| `load` | `--num_writes KEYS`, `LOAD_THREADS` writers (not timed) | inserts `KEYS` keys with `VALUE_SIZE`-byte values |
| `write` | `--num_threads_write N --num_threads_read 0` | single-row upserts of random existing keys: one Raft round to 2 of 3 replicas each |
| `read` | `--read_only --num_threads_read N` | single-row reads of random keys from the tablet leader; every value is verified |
| `mixed` | `N/2` writers + `N/2` readers (1 → 1 + 1) | both at once |

Each timed run lasts `DURATION` seconds, once per `THREADS` count (`1 8 32`). The workload uses
YugabyteDB's fork of the Cassandra Java driver, whose partition-aware policy sends every statement
straight to the node that leads the key's tablet. YCQL always reads and writes at the tablet
leader (the tool's default `QUORUM` is not a tunable here: there is no eventual consistency to
choose), so unlike the ScyllaDB example's cassandra-stress runs there is no `ONE` vs `QUORUM` vs
`ALL` trade-off to measure; `--local_reads` (follower reads at `ONE`) is not used.

yb-sample-apps prints ops/s and the mean latency per 5 s interval and, with
`--output_json_metrics`, cumulative latency statistics of every operation. The jar's own
`log4j.properties` logs every driver request at TRACE, which caps throughput at a few thousand
ops/s, so [`bench/log4j.properties`](bench/log4j.properties) replaces it. The report
([`bench/report_ycql.py`](bench/report_ycql.py)) takes ops/s and the mean from the second status
line to the last (leaving out connection setup and the `count(*)` the tool runs first) and p99 and
max from the JSON; the tool computes no other percentiles.

```bash
make benchmark-ycql                     # 1M keys x 100 B, 60 s per run, 1/8/32 threads
make benchmark-ycql SMOKE=1             # 100k keys, 10 s per run
make benchmark-ycql DURATION=120 THREADS="4 64" KEYS=5000000 VALUE_SIZE=1024
```

It prints a summary table (ops/s, mean, p99 and max latency, exceptions per run and operation)
and keeps the raw output and a parsed JSON (versions, live tservers, RF, parameters, limits,
Docker VM) in the gitignored `results/yugabyte-ycql-<UTC time>.{txt,json}`, with the same
resource budget as `make benchmark`.

### Sample results

2026-09-28, `make benchmark-ycql` (defaults: 1M keys x 100 B, 60 s per run), Docker Desktop
29.5.3 on an Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64, native arm64 images),
YugabyteDB 2026.1.2.0, 3 nodes RF=3, 2 CPUs / 4 GB per node, client 2 CPUs (Temurin 17).

| workload | threads | op | ops/s | mean ms | p99 ms |
|---|---|---|---|---|---|
| load | 32 | write (insert) | 22,255 | 1.31 | 7.58 |
| write | 1 | write | 1,796 | 0.56 | 0.96 |
| write | 8 | write | 10,387 | 0.77 | 2.23 |
| write | 32 | write | 24,059 | 1.32 | 11.42 |
| read | 1 | read | 4,637 | 0.21 | 0.31 |
| read | 8 | read | 25,876 | 0.31 | 0.49 |
| read | 32 | read | 55,721 | 0.57 | 4.50 |
| mixed | 1 | read / write | 3,549 / 1,734 | 0.28 / 0.57 | 0.38 / 0.71 |
| mixed | 8 | read / write | 12,092 / 5,558 | 0.33 / 0.72 | 0.61 / 2.18 |
| mixed | 32 | read / write | 25,047 / 10,412 | 0.63 / 1.53 | 7.93 / 16.92 |

No errors. The driver sends every statement straight to the key's tablet leader. A read is
answered there (0.21 ms with 1 thread). A write costs ~2.7x as much: one Raft round to a second
replica, and no distributed transaction, unlike TPC-B. Reads reach ~56k ops/s with 32 threads
and writes ~24k. Past 8 threads, p99 grows faster than
throughput: the three nodes and the client share 8 capped CPUs.

### Future work

Add sysbench (`--db-driver=pgsql`) with one common workload set (same scripts, table count/size,
thread counts and duration) shared by every sysbench-capable example — TiDB, OceanBase (single node
and cluster), SingleStore, RonDB, YugabyteDB YSQL and CedarDB — so their numbers compare directly.
This example currently uses its own tool and parameters.

Also run one standardized benchmark everywhere: TPC-C (e.g. with [go-tpc](https://github.com/pingcap/go-tpc),
which speaks MySQL and PostgreSQL, or [BenchBase](https://github.com/cmu-db/benchbase)) with the same number
of warehouses, threads, duration and think-time setting for every system. The warehouse count sets the data
size and the contention: with the spec's keying/think times, throughput is capped at about 12.86 tpmC per
warehouse (the YDB example's 10-warehouse run reached 127 tpmC, i.e. that cap, not its limit), so a comparison
needs either enough warehouses or think time disabled, applied the same way to each database.
