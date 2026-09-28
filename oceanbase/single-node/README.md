# OceanBase — single node

One OceanBase Community Edition observer from the official
[`oceanbase/oceanbase-ce`](https://github.com/oceanbase/docker-images/tree/main/oceanbase-ce)
image, as in the
[Docker quick start](https://en.oceanbase.com/docs/common-oceanbase-database-10000000001970931).
`MODE=mini` (the image default) runs `obd` inside the container to deploy one observer
with the smallest resource settings, then creates the MySQL-mode user tenant `test`.
Image `4.4.2.1-101000022026050611` (tag `4.4.2-lts`), multi-arch, so it runs natively on
Apple silicon.

```bash
make up        # start and wait for "boot success!" (about 1 minute here, up to 5)
make test      # run sql/*.sql: HASH/RANGE partitions, generated data, transaction, EXPLAIN pruning + PX plan, internal views
make benchmark # sysbench OLTP (point_select, read_only, read_write) against tenant `test`; SMOKE=1 = quick
make status    # servers and tenants from the sys tenant
make cli       # interactive obclient as root@test
make cli-sys   # interactive obclient as root@sys (cluster-wide views)
make logs      # last 50 lines of the entrypoint (obd) output
make down      # remove the container and its data
```

- MySQL protocol: `localhost:2881` (`OB_PORT=... make up` to change the host port)
  - `mysql -h127.0.0.1 -P2881 -uroot@test` — user tenant `test`, no password
  - `mysql -h127.0.0.1 -P2881 -uroot@sys` — sys tenant, no password (cluster administration)
- `sql/*.sql` run as `root@test`; `sql/*.sys.sql` run as `root@sys`, where the
  cluster-wide views (`DBA_OB_TENANTS`, `GV$OB_SERVERS`, `CDB_OB_TABLE_LOCATIONS`,
  unit configs) live.

## Benchmark

`make benchmark` runs [sysbench](https://github.com/akopytov/sysbench) 1.0.20 (built from
[`bench/Dockerfile`](bench/Dockerfile) on Debian trixie, native on arm64 and amd64; compose
profile `bench`) over the MySQL protocol as `root@test`: it prepares `sbtest` tables in the
`test` tenant, runs `oltp_point_select`, `oltp_read_only` and `oltp_read_write` for a fixed
time at each thread count, prints TPS, QPS, average and p95 latency per run, then drops the
tables. Raw sysbench output and a JSON summary (versions, parameters, tenant unit, Docker VM
CPUs/memory) go to the gitignored `results/`.

| Variable | Default | `SMOKE=1` |
|---|---|---|
| `BENCH_TABLES` × `BENCH_SIZE` | 4 × 50,000 rows | 2 × 10,000 |
| `BENCH_TIME` | 60 s per run | 10 s |
| `BENCH_THREADS` | `1 8 32` | `4` |

It measures OLTP on one observer: every tablet is in one log stream on one server, so
transactions commit one-phase and no replication or network hop is involved; the numbers
show the SQL layer and single-server transaction path of the `mini` tenant (3 GiB, capped
by the observer's 6G `memory_limit`), not distributed OceanBase.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) caps the `oceanbase`
container at `BENCH_CPUS=4` / `BENCH_MEM=8g` (no swap) with `docker update`, and restores the
old limits afterwards. Docker cannot remove a memory limit from a running container, so
"unlimited" comes back as the Docker VM's total memory; `make down && make up` starts clean.
**Memory is 8 GB, not the 6 GB used for the other single-node databases**: the observer's own
`memory_limit` is 6G (the smallest that bootstraps, see below), and the process needs headroom
above that. The JSON records this, with the applied limits, under `limits`. The sysbench client
has `cpus: 2` in compose (`BENCH_CLIENT_CPUS`). The CPU cap is only enforced by the cgroup:
`mini` mode sets `cpu_count` 16, so the `test` tenant still reports a 13-CPU unit and schedules
as if it had them. That is part of why 32 threads do worse than 8 on `oltp_read_write`.

### Sample results

2026-09-28, `make benchmark` (defaults: 4 × 50,000 rows, 60 s per run), Docker Desktop 29.5.3
on an Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64, native arm64 image), OceanBase CE
4.4.2.1 (`mini`, tenant `test` 3 GiB) capped at 4 CPUs / 8 GB, sysbench 1.0.20 on 2 CPUs.

| Workload | Threads | TPS | QPS | avg ms | p95 ms |
|---|---|---|---|---|---|
| oltp_point_select | 1 | 11,475 | 11,475 | 0.09 | 0.19 |
| oltp_point_select | 8 | 55,016 | 55,016 | 0.15 | 0.27 |
| oltp_point_select | 32 | 82,218 | 82,218 | 0.39 | 0.60 |
| oltp_read_only | 1 | 593 | 9,483 | 1.69 | 2.26 |
| oltp_read_only | 8 | 3,110 | 49,766 | 2.57 | 3.19 |
| oltp_read_only | 32 | 4,634 | 74,140 | 6.90 | 45.79 |
| oltp_read_write | 1 | 429 | 8,574 | 2.33 | 3.13 |
| oltp_read_write | 8 | 956 | 19,113 | 8.37 | 20.74 |
| oltp_read_write | 32 | 490 | 9,809 | 65.21 | 110.66 |

Reads scale well on 4 CPUs: 82k point selects/s at p95 0.6 ms. Writes peak at 8 threads
(~960 tps) and then fall by half at 32. At that point there are far more runnable threads than
the 4 capped CPUs, in a tenant that schedules as if it had 13. One `SMOKE=1` run started right after boot hit
`error 6002 (Transaction rollbacked)` on `oltp_read_write`; the full run afterwards had no errors.

## Memory and disk

Settings are in [`docker-compose.yml`](docker-compose.yml) and can be overridden with the
same variable names (`OB_MEMORY_LIMIT=... make up`).

| Setting | Value | Notes |
|---|---|---|
| `memory_limit` | 6G | image `mini` default; 4G fails creating the `test` resource pool, 5G creates the tenant but then stalls loading time zone data |
| `system_memory` | 1G | leaves 5G for tenants: `sys` 2G, `test` 3G |
| `datafile_size` | 2G | image default 5G |
| `log_disk_size` | 4G | image default 5G; `sys` and `test` get 2G each |

Give Docker Desktop at least 8 GB of RAM (OceanBase's stated minimum: 2 cores, 8 GB).
The container's resident memory was about 3.5–4 GiB after `make test`, but the observer
may grow to its 6G `memory_limit`. Idle, the observer still uses about 35% of one CPU
(background threads); it reports `CPU_CAPACITY` 16 regardless of the Docker VM's CPUs,
since `mini` sets `cpu_count` 16. It also takes about 6.5 GB of disk inside the container
(the log disk and data file are preallocated) on top of the 1.9 GB image.

Bootstrap data lives in the container layer and is discarded by `make down`.

### Future work

Run one common sysbench workload set (same scripts, table count/size, thread counts and duration)
across every sysbench-capable example — TiDB, OceanBase (single node and cluster), SingleStore and
RonDB over the MySQL protocol, YugabyteDB YSQL and CedarDB with sysbench's `pgsql` driver — so their
numbers compare directly. Today each example uses its own parameters.

Also run one standardized benchmark everywhere: TPC-C (e.g. with [go-tpc](https://github.com/pingcap/go-tpc),
which speaks MySQL and PostgreSQL, or [BenchBase](https://github.com/cmu-db/benchbase)) with the same number
of warehouses, threads, duration and think-time setting for every system. The warehouse count sets the data
size and the contention: with the spec's keying/think times, throughput is capped at about 12.86 tpmC per
warehouse (the YDB example's 10-warehouse run reached 127 tpmC, i.e. that cap, not its limit), so a comparison
needs either enough warehouses or think time disabled, applied the same way to each database.
