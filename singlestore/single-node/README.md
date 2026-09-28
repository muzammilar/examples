# SingleStore — single node

The official [SingleStore Dev Image](https://github.com/singlestore-labs/singlestoredb-dev-image)
(`ghcr.io/singlestore-labs/singlestoredb-dev:0.2.85`, engine 9.1.1 from the RC channel) on
Docker Compose. One container runs a two-process cluster: a master aggregator (port 3306) and
one leaf (port 3307), plus SingleStore Studio and the Data API.

```bash
make up        # start, wait for the healthcheck, print version, SHOW AGGREGATORS / SHOW LEAVES
make test      # run sql/*.sql: reference / rowstore / columnstore tables with SHARD KEY + SORT KEY,
               # 50k customers + 600k orders generated server-side, colocated vs broadcast join
               # (EXPLAIN), PROFILE, window function, segment compression + block elimination,
               # hash-index point lookup, UPDATE/DELETE on columnstore, rowstore transaction,
               # JSON (::$ / ::%), VECTOR with <*> (DOT_PRODUCT) and <-> (EUCLIDEAN_DISTANCE),
               # partitions per leaf
make benchmark # sysbench OLTP (point_select, read_only, read_write) + timed columnstore aggregations
make status    # container state, leaves, databases, rows/memory per table
make cli       # interactive client
make down      # remove the container, its volume and the built bench image
```

- MySQL protocol: `localhost:3336` (`mysql -h 127.0.0.1 -P 3336 -u root -pSingleStore-Demo-1`)
- Studio (web UI): http://localhost:18080, [Data API](https://docs.singlestore.com/db/v9.0/reference/data-api/) (SQL over HTTP): http://localhost:19000
- Host ports default to 3336 / 18080 / 19000 to stay clear of a local MySQL and other
  services; override with `SINGLESTORE_PORT`, `SINGLESTORE_STUDIO_PORT`, `SINGLESTORE_HTTP_PORT`.
- Root password: `SINGLESTORE_PASSWORD` (default `SingleStore-Demo-1`), passed to both
  `docker compose` and the Makefile.

Licensing: no license key needed. Without `SINGLESTORE_LICENSE` the image applies a free
license that is built into it (see its [`start.sh`](https://github.com/singlestore-labs/singlestoredb-dev-image/blob/main/scripts/start.sh)),
valid for "development, prototyping, and functional testing" on up to 8 cores / 64 GB
(hence `cpus: 8` in the compose file), and since 0.2.40 each database has at most two
partitions. Set `SINGLESTORE_LICENSE` to use your own key.

The image is amd64 only (`platform: linux/amd64`). On Apple silicon it runs under Rosetta and
needs x86-64-v3 support, i.e. macOS 26 or newer (tested on macOS 26.5, Docker Desktop).

## Benchmark

`make benchmark` ([`bench/run.sh`](bench/run.sh)) runs from an image built from
[`bench/Dockerfile`](bench/Dockerfile) (`debian:trixie-slim` + Debian's `sysbench` 1.0.20, native
arch) and talks to the master aggregator over the MySQL protocol on the compose network:

1. **sysbench OLTP.** `TABLES` tables (default 4) of `TABLE_SIZE` rows (50,000) in database
   `sbtest`, created as `TABLE_TYPE` (default `rowstore`: in-memory, lock-free skiplist indexes;
   `columnstore` for universal storage). sysbench's `CREATE TABLE` has no table type, so
   `default_table_type` (a GLOBAL-only variable) is switched for the run and put back afterwards.
   Then `oltp_point_select` (one primary-key lookup per transaction), `oltp_read_only` (10 pk
   lookups + 4 range queries) and `oltp_read_write` (the same plus 2 updates, a delete and an
   insert, committed across both partitions), each for `DURATION` s (60) with `THREADS` clients (8).
2. **Columnstore analytics.** [`bench/analytics.lua`](bench/analytics.lua) runs six queries `RUNS`
   times each (5) on the 600,000-row columnstore `demo.orders` from `sql/02-generate.sql` (loaded
   first if it is missing): a full-table aggregate, `GROUP BY` month, `COUNT(DISTINCT)`, a `GROUP BY`
   on a JSON key, a one-week `created_at` range (the `SORT KEY` lets it skip segments/blocks) and
   the colocated three-way join from `sql/03`. It reports the first run (which can include
   compiling the query plan) and the median/min of the others.

What it shows: the same engine serving in-memory rowstore OLTP (sub-millisecond point lookups) and
columnstore scans/aggregations over the same MySQL connection.

```bash
make benchmark                        # 4 x 50,000 rows, 60 s per workload, 8 threads, 5 runs per query
make benchmark SMOKE=1                # 2 x 10,000 rows, 10 s per workload, 3 runs per query
make benchmark TABLE_TYPE=columnstore THREADS=16 DURATION=120
```

It prints a summary table (transactions/s, queries/s, avg/p50/p99 latency, sysbench's ignored and
retried errors; per query first/warm times) and keeps the raw sysbench output plus parsed JSON with
the versions, parameters and Docker VM CPUs/memory in `results/singlestore-<UTC time>.{txt,json}`
(gitignored), written by [`bench/report.py`](bench/report.py) (standard library, `uv run --frozen`
in `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`). `DROP DATABASE sbtest` runs at the end,
also when a workload fails.

Keys are picked with `--rand-type=uniform` (`RAND_TYPE`). With sysbench's default `special`
distribution most transactions hit the same 1% of rows; in `oltp_read_write` two transactions then
regularly lock rows on the two partitions in opposite order, and since the deadlock detector is off
by default (`enable_deadlock_detector`, startup-only) such a cycle is only broken by
`lock_wait_timeout` (30 s): throughput drops to near zero for half a minute.

**Not representative on Apple silicon.** The Dev Image is amd64 only, so on an arm64 Mac the server
runs under Rosetta emulation (the report prints the server image's platform and a warning), in the
same Docker VM as the client, with the free license's limits (8 cores, 2 partitions per database).
The numbers show the example working, not SingleStore's performance.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) caps the `singlestore`
container at `BENCH_CPUS=4` / `BENCH_MEM=6g` (no swap) with `docker update`. Afterwards it
restores the compose limits, i.e. `cpus: 8` (the free mode's core limit) and "unlimited" memory,
which comes back as the Docker VM's total because Docker cannot remove a memory limit;
`make down && make up` starts clean. The sysbench client has `cpus: 2` in compose
(`BENCH_CLIENT_CPUS`). The JSON records the applied limits under `limits`. SingleStore sets its
`maximum_memory` and thread counts when the node starts, from what it sees then: 8 CPUs and the
VM's memory. So the 6 GB cap is only enforced by the cgroup, not by SingleStore's own memory
accounting. It used ~1.4 GB here.

### Sample results

2026-09-28, `make benchmark` (defaults), Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM:
11 CPUs, 24.4 GB, aarch64). **The server is the amd64 image under Rosetta emulation**, so these
numbers understate SingleStore on real x86-64. SingleStore 9.1.1 (Dev Image 0.2.85) capped at
4 CPUs / 6 GB, client 2 CPUs (native arm64 sysbench), 8 threads, 60 s per workload.

| workload | tps | qps | avg ms | p50 ms | p99 ms |
|----------|----:|----:|-------:|-------:|-------:|
| oltp_point_select | 28,280 | 28,280 | 0.28 | 0.17 | 0.35 |
| oltp_read_only | 1,156 | 18,498 | 6.92 | 3.62 | 53.85 |
| oltp_read_write | 940 | 18,806 | 8.51 | 4.57 | 54.83 |

| query (600k-row columnstore) | first ms | warm median ms |
|-------|---------:|---------------:|
| scan_aggregate | 104.0 | 115.5 |
| group_by_month | 376.6 | 191.0 |
| count_distinct | 66.8 | 91.8 |
| json_group_by | 203.9 | 149.4 |
| range_one_week | 21.8 | 3.8 |
| colocated_join | 114.0 | 50.9 |

Point selects on the in-memory rowstore are fast even under emulation (28k/s, p99 0.35 ms). The
range and aggregate statements in `oltp_read_only` / `read_write` hit a ~54 ms p99 tail, and the
columnstore scans cost 50-190 ms warm. A range query that the sort key prunes (`range_one_week`)
takes 3.8 ms. Expect much better numbers on native x86-64.
