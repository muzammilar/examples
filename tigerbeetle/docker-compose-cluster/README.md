# TigerBeetle — 3-replica cluster with Docker Compose

Three TigerBeetle replicas of one cluster on Docker Compose, with a primary failover test.

## Quick start

```bash
make up        # start the replicas (they format on first start and elect a primary)
make test      # client/demo.py via the Python client against all three addresses, then the REPL
make failover  # stop the primary: view change, transfer 501 commits on the other two; restart it,
               # stop another replica, transfer 502 (reverses 501) commits via the old primary;
               # restart everything
make benchmark # tigerbeetle benchmark against the 3 replicas: 1M transfers (SMOKE=1: 100k)
make status    # container state, current primary, each replica's last view/role
make cli       # interactive tigerbeetle repl
make down      # remove containers, the data volumes and the built test client image
```

Three replicas of cluster `0`, as in the [Docker Compose recipe](https://docs.tigerbeetle.com/operating/deploying/docker/#run-a-multi-node-cluster-using-docker-compose):
each formats its own data file (`--replica=N --replica-count=3`) on first start, then runs
`tigerbeetle start --addresses=<all three replicas>`. Three replicas replicate every commit to a
quorum of 2 and survive one failed replica; the docs recommend
[6 replicas on separate machines](https://docs.tigerbeetle.com/operating/cluster/) for production.

- Replica `i` is `tigerbeetle-i` at `10.203.53.1i:3000` on the `tigerbeetle` network; host ports
  `3033`, `3034`, `3035` (override with `TIGERBEETLE_PORT_0..2`). A host client passes
  `127.0.0.1:3033,127.0.0.1:3034,127.0.0.1:3035` — the order must match the replica indexes.
- `--addresses` takes IPs only, identical and in replica order on every replica and client. The
  docs use `network_mode: host`; this example gives each replica a static IP on a bridge network
  (`10.203.53.0/24`) instead, which also works on Docker Desktop.
- The primary of view `v` is replica `v mod 3`; `make status` derives it from the replicas'
  `transition_to_normal … view=` log lines.
- Image `ghcr.io/tigerbeetle/tigerbeetle:0.17.9` (override with `TIGERBEETLE_VERSION`); test client
  `python:3.13-slim` + `pip install tigerbeetle==0.17.9`. Server and client need io_uring
  (`security_opt: seccomp=unconfined`); `cap_add: IPC_LOCK` allows memory locking.
- `--cache-grid=256MiB` per replica; each replica still allocates ~2.3 GiB (~7 GiB in total).

## Benchmark

`make benchmark` runs TigerBeetle's own load generator, `tigerbeetle benchmark`
([`bench/run.sh`](bench/run.sh)), in a container on the cluster network against all three
replicas. It creates `ACCOUNTS` accounts (default 10,000) and commits `TRANSFERS` transfers
between random pairs of them (default 1,000,000; `SMOKE=1`: 100,000) from `CLIENTS` clients
(1), each request a batch of up to `BATCH` transfers (8,189, the most one request holds), then
runs 100 `get_account_transfers` queries.

What it shows: TigerBeetle's throughput comes from batching. Every request is one consensus
round (prepare to the backups, a quorum of 2 of 3 acks, commit) and one pass of the double-entry
state machine for the whole batch, so thousands of transfers share the cost of one network round
trip and one disk write; the latency it reports is per batch, not per transfer. The single-node
example ([`../single-node`](../single-node)) has the same `make benchmark`, showing what
replication to a quorum costs.

```bash
make benchmark                          # 1M transfers
make benchmark SMOKE=1                  # 100k transfers
make benchmark TRANSFERS=5000000 CLIENTS=4 BATCH=1000
```

It prints a summary table (transfers/s = the tool's "load accepted", batch latency p50/p99/p100,
query latency) and keeps the raw output plus parsed JSON with the version, parameters and Docker
VM CPUs/memory in `results/tigerbeetle-cluster-<UTC time>.{txt,json}` (gitignored), written by
[`bench/report.py`](bench/report.py) (standard library, `uv run --frozen` in
`ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`). The benchmark's accounts live on ledger 2
with time-based ids, so they never collide with `client/demo.py` (ledger 1, ids 1-3) and
`make test` still passes afterwards. All three replicas and the client share one Docker VM (and
its disk), so this measures the example, not TigerBeetle on dedicated machines.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) gives the three
replicas `BENCH_CPUS=6` / `BENCH_MEM=12g` in total, 2 CPUs / 4 GB each (no swap)
with `docker update` and restores the old limits afterwards. Docker cannot remove a memory limit from a running
container, so "unlimited" goes back as the Docker VM's total memory; `make down && make up`
starts clean. The bench client has `cpus: 2` in compose (`BENCH_CLIENT_CPUS`). The JSON records
the applied limits under `limits`. A replica allocates its ~2.3 GiB up front and runs its state
machine on a single core, so the memory cap is well above that floor and the extra CPUs mostly go
to I/O.

### Sample results

2026-09-28, `make benchmark` (defaults), Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM:
11 CPUs, 24.4 GB, aarch64, native image), TigerBeetle 0.17.9, 6 CPUs / 12 GB in total, 2 CPUs / 4 GB per replica,
client 2 CPUs.

| setup | transfers | transfers/s | batch p50 ms | batch p99 ms | lookup p99 ms |
|-------|----------:|------------:|-------------:|-------------:|--------------:|
| 3 replicas (this example) | 1,000,000 | 380,543 | 14 | 50 | 58 |
| 1 replica ([single-node](../single-node)) | 1,000,000 | 689,128 | 6 | 26 | 25 |

`lookup` is `get_account_transfers`. A 3-replica cluster commits each batch on a quorum before
it acks. That costs ~45% of the single replica's throughput and roughly doubles batch latency.
Each run takes only 1.5 to 2.6 s, so repeat it, or raise `TRANSFERS`, before comparing small
differences.
