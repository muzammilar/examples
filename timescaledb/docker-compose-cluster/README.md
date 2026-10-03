# TimescaleDB — Patroni cluster (primary + 2 streaming replicas)

Three TimescaleDB nodes (`timescale/timescaledb-ha`, which bundles
[Patroni](https://patroni.readthedocs.io/)), a 3-member etcd cluster for leader election, and
HAProxy in front. One node is the primary; the other two replicate it with PostgreSQL streaming
replication.

Quick start:

```bash
make up          # etcd x3, tsdb1..3 (Patroni bootstraps one, the others clone it), HAProxy; patronictl list
make test        # sql/01-primary.sql via :5000 (primary), sql/02-replica.sql via :5001 (a replica)
make failover    # 60 s of inserts; docker stop the primary after 10 s; promotion; old primary rejoins
make switchover  # planned role change (TO=tsdb2 to pick the candidate)
make sync        # Patroni synchronous_mode: commits wait for one replica  (make async: back)
make benchmark   # ingest with async vs sync replication, reads on primary vs replicas
make status      # containers, patronictl list, pg_stat_replication
make cli         # psql on the primary   (make cli-ro: on a replica)
make down        # remove containers and volumes
```

**This is replication, not sharding.** Every node stores the whole database, so writes go to
one primary and do not scale out. Replicas give you failover and more read capacity. TimescaleDB's
own multi-node (distributed hypertables) was deprecated in 2.13 and removed in 2.14; there is no
built-in way to spread one hypertable over several servers any more. Hypertables, columnstore
chunks and continuous aggregates replicate like any other table, because they are tables.

| endpoint | host port | what |
|---|---|---|
| HAProxy :5000 | `localhost:5460` | the primary (read-write): the server whose Patroni `GET /primary` returns 200 |
| HAProxy :5001 | `localhost:5461` | the replicas, round-robin (read-only): `GET /replica` returns 200 |
| HAProxy stats | `localhost:7460` | backend health |

User `postgres`, password `tsdb-demo` (`PGPASSWORD=tsdb-demo psql -h localhost -p 5460 -U postgres`).
Override ports with `TSDB_RW_PORT`, `TSDB_RO_PORT`, `TSDB_STATS_PORT`.

- Images: `timescale/timescaledb-ha:pg18.6-ts2.30.2` (PostgreSQL 18.6, TimescaleDB 2.30.2,
  toolkit 1.26.0, Patroni 4.1.5), `quay.io/coreos/etcd:v3.6.15`, `haproxy:3.2-alpine`. All are
  multi-arch and run natively on Apple silicon.
- [`config/patroni.yml`](config/patroni.yml) is shared by all three nodes. The per-node name and
  addresses come from `PATRONI_*` environment variables. The `bootstrap.dcs` part goes into etcd
  once: `ttl: 20`, `loop_wait: 5`, `use_pg_rewind`, `use_slots` (a replication slot per replica),
  `maximum_lag_on_failover: 1 MB`, and Postgres parameters including
  `shared_preload_libraries: timescaledb` and `shared_buffers: 512MB`.
  [`config/post-bootstrap.sh`](config/post-bootstrap.sh) creates the `timescaledb` and
  `timescaledb_toolkit` extensions once, on the first primary.
- Patroni's container entrypoint is the image's `/docker-entrypoint.sh` with
  `patroni /config/patroni.yml` as the command. The image's own initdb scripts (timescaledb-tune
  and so on) do not run under Patroni.

What `make test` shows: both replicas `streaming` (`async`) through slots `tsdb1`/`tsdb2`; 1M rows
inserted, 6 chunks converted to the columnstore and a continuous aggregate created on the primary.
On a replica (`pg_is_in_recovery() = t`), the same row count, the aggregate's rows, and a
`ColumnarScan` + `VectorAgg` plan; `INSERT` fails with
`ERROR: cannot execute INSERT in a read-only transaction`. TimescaleDB's background jobs (policies,
aggregate refreshes) are defined everywhere but run only on the primary.

### Failover

`make failover` ([`failover.sh`](failover.sh)) runs [`bench/writer.sh`](bench/writer.sh), which
inserts a numbered row through HAProxy :5000, one new connection per insert, for 60 s. After 10 s
it `docker stop`s the current primary, waits for Patroni to promote a replica, and starts the old
primary again 10 s later. Then it checks that every acknowledged id is on the new primary.
2026-10-02 run:

```
==> 06:37:24 docker stop tsdb3 (the primary)
==> 06:37:41 tsdb1 is the new primary, 17.4 s after the stop
| tsdb1  | tsdb1 | Leader  | running   |  2 |   ...
| tsdb2  | tsdb2 | Replica | streaming |  2 |   ...
==> 06:37:52 docker start tsdb3
==> 06:37:58 tsdb3 is back                  # rejoined as a replica on timeline 2
==> writer: 1723 inserts acknowledged, 73 attempts failed
    writes failed for 18.2 s (first to last failed attempt)
  73 psql: error: connection to server at "haproxy" (172.20.0.8), port 5000 failed: server closed the connection unexpectedly
==> rows on the new primary: 1723; acknowledged but missing: 0
```

- Postgres on the stopped node shuts down in under a second, but the leader key stays in etcd
  until its TTL (20 s) runs out (see Known issues in [`../README.md`](../README.md#known-issues)).
  The replicas promote only then, so writes are unavailable for about the TTL. A
  crash would also wait for the TTL. Lower `ttl`/`loop_wait` fail over faster but risk
  false failovers on a busy laptop.
- `make switchover` (planned) moves the primary in ~6 s end to end. The old primary demotes
  itself first, so nothing waits on a TTL.
- No acknowledged insert was lost here: the clean shutdown streamed the last WAL to the replicas
  before the promotion. After a real crash, asynchronous replication can lose the last
  acknowledged commits (up to `maximum_lag_on_failover` bytes). `make sync` turns on
  `synchronous_mode`, in which a commit returns only after a synchronous standby has it, and
  Patroni promotes only that standby.

## Benchmark

`make benchmark` ([`bench/run.sh`](bench/run.sh), timescaledb-ha image, through HAProxy) loads
1000 devices x 2 days x 1/minute = 2.88M rows (a CSV generated in Postgres) with
`timescaledb-parallel-copy` (4 workers, `COPY` batches of 5,000) into a fresh hypertable. It
does this into the rowstore (`--disable-direct-compress`, with a `(device_id, time DESC)` index)
and straight into the columnstore (the default), once with asynchronous replication and once with
Patroni's `synchronous_mode` (switched with `PATCH /config` on the REST API). For each load it
records rows/s, the WAL the primary wrote, and how long both replicas took to replay all of it
after the load finished. Then pgbench runs one read query (one random device's last day, hourly
`time_bucket`, on the columnstore table) with 8 clients for 20 s, against the primary alone
(:5000) and against the two replicas (:5001, connections spread round-robin).

```bash
make benchmark                       # 2.88M rows, async + sync, reads 20 s each
make benchmark SMOKE=1               # 1 day, 5 s reads
make benchmark DAYS=3 WORKERS=8 MODES=sync CLIENTS=16
```

Results go to `results/timescaledb-cluster-<UTC time>.{txt,json}` (gitignored) via
[`bench/report.py`](bench/report.py). [`bench/limits.sh`](bench/limits.sh) caps the three
database containers at `BENCH_CPUS=6` / `BENCH_MEM=12g` in total (2 CPUs / 4 GiB each) for the
run and restores them afterwards. The bench client has 2 CPUs.

### Sample results

2026-10-02, Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64),
PostgreSQL 18.6, TimescaleDB 2.30.2, Patroni 4.1.5, each node capped at 2 CPUs / 4 GiB. The
Docker VM was shared with other stacks.

| replication | ingest into | rows/s | seconds | WAL written | table size | replicas caught up after |
|---|---|---:|---:|---:|---:|---:|
| async | rowstore | 580,060 | 5.0 | 566 MiB | 335 MiB | 29 ms |
| async | columnstore (direct compress) | 2,469,983 | 1.2 | 41 MiB | 43 MiB | 43 ms |
| sync | rowstore | 532,249 | 5.4 | 585 MiB | 336 MiB | 19 ms |
| sync | columnstore (direct compress) | 2,555,457 | 1.1 | 41 MiB | 43 MiB | 22 ms |

| reads (8 clients, 20 s) | queries/s | avg latency |
|---|---:|---:|
| primary only (:5000) | 7,992 | 1.00 ms |
| 2 replicas (:5001) | 16,068 | 0.50 ms |

- **The columnstore helps replication too.** A load straight into the columnstore writes 14x less
  WAL (41 vs 566 MiB for 2.88M rows), so the replicas receive and replay 14x less. Ingest is
  4-5x faster than into the rowstore.
- **Synchronous replication costs almost nothing for bulk loads.** Each 5,000-row `COPY` commit
  waits for one replica, and that round trip is small next to the batch. The two modes are within
  noise (±10% between runs). The replicas had replayed everything within 20-45 ms of the last
  commit either way.
- **Reads scale with replicas.** Two replicas served 2x the queries of the primary alone, which
  is the extra CPU (4 vs 2) and nothing more. Writes cannot scale this way: there is one primary.
