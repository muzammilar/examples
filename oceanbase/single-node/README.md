# OceanBase — single node

One OceanBase CE observer (`MODE=mini`, MySQL-mode tenant `test`) on Docker Compose, with SQL
tests and a sysbench benchmark.

## Quick start

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

## Setup

- Image: official [`oceanbase/oceanbase-ce`](https://github.com/oceanbase/docker-images/tree/main/oceanbase-ce)
  `4.4.2.1-101000022026050611` (tag `4.4.2-lts`), multi-arch (native on Apple silicon), as in the
  [Docker quick start](https://en.oceanbase.com/docs/common-oceanbase-database-10000000001970931).
- `MODE=mini` (image default) runs `obd` inside the container to deploy one observer with the
  smallest resource settings, then creates the MySQL-mode user tenant `test`.

| Connection | Command |
|---|---|
| user tenant `test`, no password | `mysql -h127.0.0.1 -P2881 -uroot@test` |
| sys tenant, no password (cluster admin) | `mysql -h127.0.0.1 -P2881 -uroot@sys` |

Host port: `OB_PORT=... make up`. `sql/*.sql` run as `root@test`; `sql/*.sys.sql` run as `root@sys`,
where the cluster-wide views live (`DBA_OB_TENANTS`, `GV$OB_SERVERS`, `CDB_OB_TABLE_LOCATIONS`, unit configs).

## Benchmark

`make benchmark` runs [sysbench](https://github.com/akopytov/sysbench) 1.0.20 (built from
[`bench/Dockerfile`](bench/Dockerfile) on Debian trixie, native on arm64 and amd64; compose profile
`bench`) as `root@test`: prepare `sbtest` tables, run `oltp_point_select`, `oltp_read_only`,
`oltp_read_write` for a fixed time per thread count, print TPS, QPS, avg and p95 latency, drop the
tables. Raw output and a JSON summary (versions, parameters, tenant unit, Docker VM CPUs/memory) go
to the gitignored `results/`.

| Variable | Default | `SMOKE=1` |
|---|---|---|
| `BENCH_TABLES` × `BENCH_SIZE` | 4 × 50,000 rows | 2 × 10,000 |
| `BENCH_TIME` | 60 s per run | 10 s |
| `BENCH_THREADS` | `1 8 32` | `4` |

Scope: one observer, every tablet in one log stream on one server, so transactions commit
one-phase with no replication or network hop. The numbers show the SQL layer and single-server
transaction path of the `mini` tenant (3 GiB, capped by the observer's 6G `memory_limit`), not
distributed OceanBase.

Resource budget:

- [`bench/limits.sh`](bench/limits.sh) caps the `oceanbase` container at `BENCH_CPUS=4` /
  `BENCH_MEM=8g` (no swap) with `docker update` and restores the old limits afterwards. Docker cannot
  remove a memory limit from a running container, so "unlimited" comes back as the Docker VM's total
  memory; `make down && make up` starts clean.
- **Memory is 8 GB, not the 6 GB used for the other single-node databases**: the observer's
  `memory_limit` is 6G (the smallest that bootstraps, see below) and the process needs headroom.
  The JSON records the applied limits under `limits`.
- sysbench client: `cpus: 2` in compose (`BENCH_CLIENT_CPUS`).
- The CPU cap is enforced only by the cgroup: `mini` sets `cpu_count` 16, so the `test` tenant
  reports a 13-CPU unit and schedules as if it had them. That is part of why 32 threads do worse
  than 8 on `oltp_read_write`.

### Sample results

2026-09-28, defaults, Docker Desktop 29.5.3 on Apple M4 Pro (Docker VM 11 CPUs, 24.4 GB, aarch64,
native arm64 image), OceanBase CE 4.4.2.1 (`mini`, tenant `test` 3 GiB) capped at 4 CPUs / 8 GB,
sysbench 1.0.20 on 2 CPUs.

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

- Reads scale on 4 CPUs: 82k point selects/s at p95 0.6 ms.
- Writes peak at 8 threads (~960 tps) and halve at 32: far more runnable threads than the 4 capped
  CPUs, in a tenant that schedules as if it had 13.

## Memory and disk

Settings are in [`docker-compose.yml`](docker-compose.yml); override with the same variable names
(`OB_MEMORY_LIMIT=... make up`).

| Setting | Value | Notes |
|---|---|---|
| `memory_limit` | 6G | image `mini` default; 4G fails creating the `test` resource pool, 5G creates the tenant but then stalls loading time zone data |
| `system_memory` | 1G | leaves 5G for tenants: `sys` 2G, `test` 3G |
| `datafile_size` | 2G | image default 5G |
| `log_disk_size` | 4G | image default 5G; `sys` and `test` get 2G each |

| Measured | Value |
|---|---|
| Docker Desktop RAM | ≥ 8 GB (OceanBase's stated minimum: 2 cores, 8 GB) |
| Resident memory after `make test` | ~3.5–4 GiB; may grow to the 6G `memory_limit` |
| Idle CPU | ~35% of one CPU (background threads); `CPU_CAPACITY` 16 regardless of VM CPUs (`mini` sets `cpu_count` 16) |
| Disk in the container | ~6.5 GB (log disk and data file preallocated) + 1.9 GB image |

Bootstrap data lives in the container layer and is discarded by `make down`.

## Known issues

- One `SMOKE=1` run started right after boot hit `error 6002 (Transaction rollbacked)` on
  `oltp_read_write`; the full run afterwards had no errors.
- Shared issues (`fs.aio-max-nr`, obd disk check): [`../README.md`](../README.md#known-issues).

## Future work

- One common sysbench workload set (same scripts, table count/size, thread counts, duration) across
  every sysbench-capable example — TiDB, OceanBase (single node and cluster), SingleStore and RonDB
  over MySQL, YugabyteDB YSQL and CedarDB with sysbench's `pgsql` driver — so numbers compare
  directly. Today each example uses its own parameters.
- One standardized benchmark everywhere: TPC-C (e.g. [go-tpc](https://github.com/pingcap/go-tpc),
  which speaks MySQL and PostgreSQL, or [BenchBase](https://github.com/cmu-db/benchbase)) with the
  same warehouses, threads, duration and think-time setting for every system. The warehouse count
  sets data size and contention: with the spec's keying/think times, throughput is capped at about
  12.86 tpmC per warehouse (the YDB example's 10-warehouse run reached 127 tpmC, i.e. that cap, not
  its limit), so a comparison needs enough warehouses or think time disabled, applied the same way
  to each database.
