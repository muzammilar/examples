# ScyllaDB — 3-node cluster with Docker Compose

Three ScyllaDB nodes (1 shard, 750 MiB each, developer mode) plus Prometheus and
Grafana scraping ScyllaDB's built-in metrics endpoint (`:9180/metrics`).

```bash
make up       # start; nodes join one after another (~1–2 min)
make test     # run cql/test.cql: RF=3 keyspace, QUORUM insert, select, delete
make failover # stop scylla-3: QUORUM + LWT still work, CONSISTENCY ALL fails; restart it
make benchmark # cassandra-stress at QUORUM: write, read, mixed, plain vs LWT update (SMOKE=1: 10 s each)
make status   # nodetool status — three UN nodes
make cli      # interactive cqlsh on scylla-1
make down     # remove containers and volumes
```

`make failover` stops `scylla-3`, runs [`failover/*.cql`](failover) on `scylla-1` while it is down
(QUORUM and lightweight transactions only need 2 of 3 replicas; `CONSISTENCY ALL` fails with
`Unavailable`), then starts it again and waits until all three nodes are `UN`.

- CQL: `127.0.0.1:9042` (scylla-1), bound to localhost only: there is no authentication
  (the default `AllowAllAuthenticator`), anyone who reaches the port has full access
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (anonymous admin) → **ScyllaDB** dashboard
- `make up` also fetches the prebuilt 2026.1 Overview, Detailed and CQL dashboards and the
  latency recording rules from [scylla-monitoring@4.16.1](https://github.com/scylladb/scylla-monitoring/tree/4.16.1/grafana/build/ver_2026.1)
  into the gitignored `grafana/provisioning/dashboards/upstream/` (Grafana folder **upstream**) and
  `prometheus/upstream/`. [`prometheus.yml`](prometheus/prometheus.yml) adds the `cluster`/`dc` labels
  they select on. The upstream OS dashboard is left out: it needs node_exporter, which this
  example does not run.

A one-shot privileged `sysctl` container raises `fs.aio-max-nr` first; Docker Desktop's
default (65536) only fits two nodes. It applies to the whole Docker VM until Docker restarts.
All nodes sit in one rack, so the RF=3 keyspace triggers an "RF-rack-valid" warning.

## Benchmark

`make benchmark` runs [cassandra-stress](https://docs.scylladb.com/manual/stable/operating-scylla/admin-tools/cassandra-stress.html)
— ScyllaDB's maintained fork, which the docs point to now that it no longer ships in the server
image — from `scylladb/cassandra-stress:3.21.1` ([`bench/run.sh`](bench/run.sh)). Every workload runs
for `DURATION` seconds with `THREADS` client threads at `CONSISTENCY QUORUM` on RF=3 keyspaces:

| workload | cassandra-stress | what it measures |
|---|---|---|
| `write` | `write`, partitions `1..KEYS` in sequence | inserts of one 100-byte column; the coordinator waits for 2 of 3 replicas |
| `read` | `read`, uniform over the written partitions | single-partition reads, 2 of 3 replicas answer (digest compare) |
| `mixed` | `mixed ratio(write=1,read=1)` | 50/50 of the two |
| `plain-update` | `user` profile [`bench/lwt.yaml`](bench/lwt.yaml), `UPDATE ... WHERE k = ?` | a normal write, as the baseline for the LWT |
| `lwt-update` | the same `UPDATE` with `IF EXISTS` | a lightweight transaction: Paxos prepare/read + accept + learn round-trips |

The read and mixed runs only pick partitions the write run created. `plain-update` walks all
`LWT_KEYS` (10,000) partition seeds first and `lwt-update` picks among the same seeds, so every
`IF EXISTS` finds its row and applies (the coordinators' `scylla_storage_proxy_coordinator_cas_*`
metrics count the Paxos rounds).

What it shows: writes are cheap (a commitlog append plus a memtable insert on each replica), a
QUORUM read has to wait for two replicas, and an LWT costs several plain writes' worth of
latency and throughput. Each node runs one shard (`--smp 1`); ScyllaDB's shard-per-core design
scales throughput with `--smp` (one shard per core, no shared locks), so on a machine with spare
cores raise `--smp` and `--memory` in [`docker-compose.yml`](docker-compose.yml) and `THREADS` until
p99 climbs.

```bash
make benchmark                           # 1M partitions, 60 s per workload, 32 threads
make benchmark SMOKE=1                   # 100k partitions, 10 s per workload
make benchmark DURATION=120 THREADS=128 KEYS=5000000
```

It prints a summary table (ops/s, mean, p50/p95/p99/p99.9 latency, errors per op type) and keeps
the raw cassandra-stress output and a parsed JSON with the ScyllaDB/tool versions, node count and
flags, RF, consistency level, parameters and Docker VM CPUs/memory in the gitignored
`results/scylladb-<UTC time>.{txt,json}`. The parser, [`bench/report.py`](bench/report.py), is
standard-library Python run with `uv run --frozen` in the `ghcr.io/astral-sh/uv` image.

The three nodes and the client share one Docker VM, so the numbers compare workloads with each
other rather than measure the hardware.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) gives the three Scylla
nodes `BENCH_CPUS=6` / `BENCH_MEM=12g` in total, i.e. 2 CPUs / 4 GB each (no swap), with
`docker update`, and restores the old limits afterwards. Docker cannot remove a memory limit
from a running container, so "unlimited" comes back as the Docker VM's total memory;
`make down && make up` starts clean. Prometheus and Grafana are left unlimited. The
cassandra-stress client has `cpus: 2` in compose (`BENCH_CLIENT_CPUS`). The JSON records the
applied limits under `limits`. Scylla's own flags bind first: `--smp 1 --memory 750M` pins each
node to one shard (one reactor thread) and 750 MB, so a node uses ~1.2 CPUs and ~700 MB of its
cap. The client was the busier side, at its 2-CPU limit.

### Sample results

2026-09-28, `make benchmark` (defaults: 32 threads, 60 s per workload, 1M partitions, RF=3,
CL=QUORUM), Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64, all
images native arm64), ScyllaDB 2026.1.13, 2 CPUs / 4 GB per node (1 shard, 750 MB used),
client 2 CPUs.

| workload | op | ops/s | p50 ms | p95 ms | p99 ms | p99.9 ms |
|---|---|---|---|---|---|---|
| write | write | 52,695 | 0.30 | 2.80 | 4.10 | 30.10 |
| read | read | 40,347 | 0.60 | 1.30 | 2.20 | 23.20 |
| mixed | read | 21,027 | 0.60 | 2.40 | 5.00 | 29.90 |
| mixed | write | 21,083 | 0.30 | 1.50 | 4.00 | 28.00 |
| plain-update | plain | 59,524 | 0.30 | 0.50 | 2.80 | 48.00 |
| lwt-update | lwt | 15,471 | 1.90 | 3.00 | 4.60 | 13.80 |

Writes are cheaper than reads. A write goes to the commitlog and memtable, while a QUORUM read
has to reconcile two replicas. Lightweight transactions (Paxos: several replica round trips) cost ~6x a
plain write at p50 and cut throughput to about a quarter. One shard per node and a
client-bound load generator mean these are floor numbers for ScyllaDB.
