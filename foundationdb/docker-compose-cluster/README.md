# FoundationDB — 3-node cluster with Docker Compose

Three `fdbserver` containers in `double` redundancy on the Redwood storage engine
(`ssd-redwood-1`), all three coordinators, plus
[foundationdb-exporter](https://github.com/aikoven/foundationdb-exporter), Prometheus and Grafana.

```bash
make up       # start everything, `configure new double ssd-redwood-1`, wait until healthy
make test     # run queries/test.fdbcli: set/get, getrange with a limit,
              # begin/commit and rollback transactions
make failover # stop fdb-2: commits continue, status shows lost fault tolerance;
              # start it again and wait until fully replicated
make benchmark # transactional load generator (SMOKE=1 for a short run), see below
make status   # fdbcli status (3 machines, 3 coordinators, fault tolerance 1)
make cli      # interactive fdbcli
make down     # remove containers, volumes and the bench image
```

`make up` / `make failover` poll `fdbcli --exec 'status json'` (up to 120 x 2 s) until
`.cluster.data.state.healthy` is `true`, using the `jq` that ships in the foundationdb image.

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (anonymous admin) → **FoundationDB** dashboard
- `make up` also fetches the exporter's own dashboard from
  [aikoven/foundationdb-exporter@b8e124d](https://github.com/aikoven/foundationdb-exporter/tree/b8e124de1fd83b6467bafedb6ad438e48eaaed31/grafana)
  into the gitignored `grafana/provisioning/dashboards/upstream/` → Grafana folder **upstream**.
  It filters on a `namespace` label, which [`prometheus.yml`](prometheus/prometheus.yml) sets.

The cluster file (set via `FDB_CLUSTER_FILE_CONTENTS` and mounted into the exporter as
[`fdb.cluster`](fdb.cluster)) names the coordinators by hostname, supported since FDB 7.1.
The exporter image is amd64-only and runs under emulation on Apple silicon.

## Failover

With `double` redundancy every key lives on 2 of the 3 processes, and the 3 coordinators need a
majority (2). `make failover` stops `fdb-2`, then commits and reads a key in one transaction (the
cluster recovers onto `fdb-0`/`fdb-1` within seconds), prints `status` (fault tolerance drops to 0,
data distribution re-replicates what `fdb-2` held), starts `fdb-2` again and waits until
`.cluster.data.state.healthy`, ending with `Fault Tolerance` / `Replication health` back to normal.

## Benchmark

`make benchmark` runs [`bench/bench.py`](bench/bench.py) (Python bindings `foundationdb==7.3.79`,
pinned in `bench/uv.lock`) in a container on the compose network, built from
[`bench/Dockerfile`](bench/Dockerfile): the `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`
image plus `libfdb_c.so` copied from the `foundationdb/foundationdb:7.3.79` server image, connected
through [`fdb.cluster`](fdb.cluster). FDB's C load generator `mako` is not shipped in that image.
The uv cache and venv live in the `uv-cache` volume (kept by `make down`).

`CLIENTS` processes (each with its own FDB network thread) run each workload for `DURATION`
seconds; latency is per transaction including retries:

| workload | transaction |
| -------- | ----------- |
| load | `ROWS` keys x 100 B, 100 keys per transaction |
| point reads | 1 random `get` (includes getting a read version) |
| blind writes | 1 random `set` + commit |
| read-modify-write | read 2 random balances out of `HOT` accounts, write both (-1 / +1) |
| atomic adds | 2 atomic `add`s on the same `HOT` keys, no reads |
| range reads | `get_range` of `RANGE` (100) consecutive keys |

What it shows:

- **ACID multi-key transactions.** A read-modify-write transaction touches two keys that
  may sit on different storage servers; it commits all-or-nothing at one version. After the run
  the benchmark sums every balance: serializable isolation means no transfer is lost or applied
  twice, so the total equals `HOT x 1,000,000` exactly (and the atomic counters equal
  2 x committed transactions).
- **How conflicts behave.** FDB uses optimistic concurrency: a transaction reads at a read version
  and, at commit, the resolver rejects it (`not_committed`, error 1020) if another transaction
  committed a write to a key it read in between. The client retries (`on_error` backs off), so
  contention shows up as a conflict rate and higher p99, not as blocking or deadlocks. Lower `HOT`
  or raise `CLIENTS` to see more conflicts. Atomic adds on the same keys never conflict, because
  blind atomic operations add no read conflict ranges.

Defaults: `DURATION=30 CLIENTS=8 ROWS=100000 HOT=100`; `SMOKE=1` uses `DURATION=5 CLIENTS=4
ROWS=10000`. Override any of them, e.g. `make benchmark CLIENTS=16 HOT=20`. Results print as a
table and are written to `results/foundationdb-<timestamp>.json` and `.txt` (git-ignored) with the
server and client versions, redundancy mode, storage engine, parameters and the CPU count / memory
of the Docker VM.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) gives the three
`fdbserver` containers `BENCH_CPUS=6` / `BENCH_MEM=12g` in total, i.e. 2 CPUs / 4 GB each (no
swap), with `docker update`, and restores the old limits afterwards. Docker cannot remove a
memory limit from a running container, so "unlimited" comes back as the Docker VM's total
memory; `make down && make up` starts clean. The exporter, Prometheus and Grafana are left
unlimited. The bench client (8 processes) has `cpus: 2` in compose (`BENCH_CLIENT_CPUS`). The
JSON records the applied limits under `limits`. Each `fdbserver` is one single-threaded process,
so it can use one of its two CPUs at most. Here each used ~0.3 CPU and under 0.5 GB. The load is
bound by commit latency, not by the budget.

### Sample results

2026-09-28, `make benchmark` (defaults: 8 clients, 100,000 rows × 100 B, 30 s per workload,
hot set 100), Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64;
`fdbserver` and the bench native arm64, only the exporter image is amd64 under emulation),
FoundationDB 7.3.79, `double`, `ssd-redwood-1`, 2 CPUs / 4 GB per process, client 2 CPUs.

| workload | tx/s | keys/s | p50 (ms) | p99 (ms) | conflicts |
| -------- | ---- | ------ | -------- | -------- | --------- |
| load (100 keys/tx) | 1,570 | 157,025 | 4.89 | 7.78 | 0 |
| point reads | 3,412 | 3,412 | 2.10 | 4.95 | 0 |
| blind writes | 1,894 | 1,894 | 4.03 | 8.20 | 0 |
| read-modify-write | 1,241 | 2,482 | 4.96 | 30.85 | 5,386 (12.6%) |
| atomic adds | 2,151 | 4,302 | 3.71 | 6.55 | 0 |
| range reads (100 keys) | 2,486 | 248,644 | 3.12 | 5.77 | 0 |

Both invariant checks passed: the balances summed to 100,000,000 and the atomic counter matched.
Every transaction pays a GRV plus, for writes, a ~4 ms commit, so per-key throughput comes from
batching (157k keys/s loading 100 per tx, 249k keys/s in range reads). On 100 hot keys,
read-modify-write conflicts on 12.6% of attempts and its p99 grows to 31 ms, while atomic adds
on the same keys never conflict.
