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
(= `RECORDS`) with `THREADS` threads (16), uniform key choice. Afterwards `client cleanup-bench`
deletes both key ranges.

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

### Sample results

TODO: numbers from a quiet machine.

| workload | op | ops/s | avg ms | p99 ms |
|----------|----|------:|-------:|-------:|
| raw_a | READ | TODO | TODO | TODO |
| raw_a | UPDATE | TODO | TODO | TODO |
| raw_c | READ | TODO | TODO | TODO |
| txn_a | READ | TODO | TODO | TODO |
| txn_a | UPDATE | TODO | TODO | TODO |
| txn_c | READ | TODO | TODO | TODO |
