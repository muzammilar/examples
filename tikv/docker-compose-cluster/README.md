# TiKV — PD + 3 TiKV with Docker Compose, no TiDB

TiKV used directly as a distributed key-value store: 3 PD (placement driver: metadata,
TSO timestamps, region scheduling) and 3 TiKV stores from the multi-arch `pingcap/pd` and
`pingcap/tikv` images, pinned to `v8.5.8`. The demo client in `client/` is a small Go
program on [`tikv/client-go`](https://github.com/tikv/client-go) (the client TiDB itself
uses, at the version TiDB v8.5.8 pins), built in a `golang` container. `make up` waits
until all three stores are Up and every region has its three replicas.

```bash
make up        # start PD and TiKV, build the client, wait for 3 stores Up and full replication
make test      # run the client: RawKV put/get/scan/delete, TxnKV commit, optimistic and pessimistic conflicts
make benchmark # go-ycsb workloads A and C through RawKV and TxnKV (ops/s, p99 per operation)
make status    # pd-ctl member, store, region and replication config
make cli       # interactive pd-ctl
make down      # remove containers, volumes and the locally built client/bench images
```

The cluster needs ~8 GB of Docker memory (each TiKV settles at ~2.3 GB RSS).

- PD API: http://localhost:22379/pd/api/v1/stores (`PD_PORT` overrides); clients
  outside Docker can't reach the stores (they advertise `tikvN:20160`), so run them
  on the compose network like `make test` does
- other versions: `TIKV_VERSION=v8.5.7 make up`

The client (`client/main.go`):
- **RawKV**: `Put`, `BatchPut`, `Get`, `Scan` over a prefix, `Delete`, `DeleteRange`.
- **TxnKV**: a transaction writing three keys atomically; two optimistic transactions
  writing the same key (the second commit fails with a write conflict); a pessimistic
  transaction holding a lock while another one's no-wait `LockKeys` fails, then succeeds
  after the first commits.

Raw and transactional keys use separate prefixes (`raw/`, `txn/`): with API V1 (the
default) the two APIs must not share keys.

tikv.org's [TiKV in 5 minutes](https://tikv.org/docs/latest/concepts/tikv-in-5-minutes/)
starts a local cluster with `tiup playground --mode tikv-slim` and uses the Python
`tikv-client`. This example uses Docker Compose with pinned images instead, so only
Docker is needed, and Go instead of Python: the `tikv-client` wheels on PyPI are x86-64
only for Linux (building it on arm64 needs a Rust toolchain).

Notes:
- `config/tikv.toml` shrinks TiKV for a laptop: 256 MB block cache per store, no 5 GB
  reserved disk and a 2 GB reported capacity (on a Docker disk more than 80% full, PD
  would otherwise treat every store as low-space and not place replicas).
- A new cluster has five regions (split at the `r` and `x` API V2 keyspace prefixes) with
  all leaders on the first store; PD's balance-leader scheduler spreads them over a few minutes.

## Benchmark

`make benchmark` ([`bench/run.sh`](bench/run.sh)) runs PingCAP's
[go-ycsb](https://github.com/pingcap/go-ycsb) v1.0.3 from an image built from
[`bench/Dockerfile`](bench/Dockerfile): the published image is amd64 only, so it is built from
source in `golang:1.26-alpine`, with only its TiKV driver registered (upstream's `main` pulls in ~20
database clients, some needing cgo). Like the demo client it talks to PD for region routing and
timestamps and to the stores directly, once through each API:

- **raw** (`tikv.type=raw`, RawKV): single-key `Get` / `Put` on the region leader, no MVCC, no
  transactions (an update is a `Get` then a `Put`, not atomic).
- **txn** (`tikv.type=txn`, TxnKV): every read takes a start timestamp from PD and reads a
  snapshot; every update is an optimistic read-modify-write transaction committed with Percolator
  (one-phase or async commit when it can). Two concurrent updates of the same key make the later
  commit fail with a write conflict; go-ycsb does not retry, so they show up as `errors`.

For each: `load` inserts `RECORDS` rows (default 100,000; 10 fields of 100 bytes, table
`ycsb_raw` / `ycsb_txn`, i.e. key prefixes that keep the two APIs apart as API V1 requires), then
YCSB workload **A** (50% reads, 50% updates) and **C** (100% reads), `OPERATIONS` operations each
(= `RECORDS`) with `THREADS` threads (16), uniform key choice. TxnKV runs first: once the RawKV
load has grown past one region, PD splits it at raw (unencoded) keys, and the TxnKV client cannot
decode those region boundaries ("failed to decode region range key"), so every txn operation would
fail. For the same reason `client cleanup-bench` deletes the `ycsb_txn:` range with raw
`DeleteRange` calls on its encoded keys in each column family, not with a TxnKV scan. The raw
boundaries outlive the data, so start from a fresh `make up` before running the benchmark again.

What it shows: the price of transactions on the same store. Raw reads and writes are one RPC to the
leader (+ Raft replication for a write); transactional reads add a PD timestamp round trip, and
transactional updates add the timestamp, prewrite and commit phases, so workload A is where raw and
txn differ most.

```bash
make benchmark                        # 100,000 records, 100,000 operations per workload, 16 threads
make benchmark SMOKE=1                # 10,000 records / operations
make benchmark RECORDS=500000 THREADS=64
```

It prints a table of operations/s, avg/p50/p99 latency and failed operations per workload and
operation type (and for each load) and keeps the raw go-ycsb output plus parsed JSON with the
versions, parameters, cluster layout and Docker VM CPUs/memory in `results/tikv-<UTC time>.{txt,json}`
(gitignored), written by [`bench/report.py`](bench/report.py) (standard library, `uv run --frozen`
in `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`). Set `GOPROXY` to build go-ycsb through a
Go module proxy of your own. Everything (3 PD, 3 TiKV and the client) shares one Docker VM, so this
measures the example, not TiKV on dedicated machines.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) caps the cluster with
`docker update` (memory without swap), at `BENCH_CPUS=6` / **`BENCH_MEM=14g`**: `tikv0`-`tikv2`
1.5 CPUs / 4.17 GB each, `pd0`-`pd2` 0.5 CPU / 512 MB each. **Memory is 14 GB, not the 12 GB used
for the other clusters.** TiKV sizes its memory usage limit and write buffers from the machine at
startup (the Docker VM here), and at 2.8 GB per store TiKV was OOM-killed in the
[TiDB example](../../tidb/docker-compose-cluster). Here the stores peaked at 2.6-2.9 GB. The JSON
records this, with the applied limits, under `limits`. The old limits come back afterwards. Docker
cannot remove a memory limit from a running container, so "unlimited" returns as the Docker VM's
total memory; `make down && make up` starts clean. The go-ycsb client has `cpus: 2` in compose
(`BENCH_CLIENT_CPUS`).

### Sample results

2026-09-28, `make benchmark` (defaults: 100,000 records, 100,000 operations per workload,
16 threads), Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64,
native arm64 images), TiKV/PD 8.5.8, split as above, client 2 CPUs.

| workload | op | ops/s | avg ms | p50 ms | p99 ms |
|----------|----|------:|-------:|-------:|-------:|
| raw load | INSERT | 8,355 | 1.91 | 1.83 | 2.81 |
| raw_a | READ | 4,960 | 0.29 | 0.18 | 0.82 |
| raw_a | UPDATE | 4,985 | 2.90 | 2.03 | 30.85 |
| raw_c | READ | 27,369 | 0.58 | 0.22 | 2.12 |
| txn load | INSERT | 7,428 | 2.15 | 2.02 | 3.47 |
| txn_a | READ | 4,032 | 0.50 | 0.37 | 0.93 |
| txn_a | UPDATE | 4,010 | 3.46 | 2.54 | 29.20 |
| txn_c | READ | 20,503 | 0.79 | 0.42 | 2.09 |

Transactions cost less than expected on one store. A txn read adds a PD timestamp (p50 0.37 ms vs
0.18 ms raw), and a Percolator update is only ~25% slower than a raw Raft write (p50 2.5 ms vs
2.0 ms), thanks to one-phase/async commit. Only 1 write conflict occurred in 50k txn updates.
Writes are bound by Raft replication. The data fits a few regions, so one store holding their
leaders did most of the work (~1.6 CPUs vs ~0.3 on the others).
