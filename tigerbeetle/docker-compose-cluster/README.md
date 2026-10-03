# TigerBeetle — 3-replica cluster with Docker Compose

Three TigerBeetle replicas of one cluster on Docker Compose, with a primary failover test.

## Quick start

```bash
make up        # start the replicas (they format on first start and elect a primary)
make test      # client/demo.py via the Python client against all three addresses, then the REPL
make failover  # see below
make benchmark # tigerbeetle benchmark against the 3 replicas: 1M transfers (SMOKE=1: 100k)
make status    # container state, current primary, each replica's last view/role
make cli       # interactive tigerbeetle repl
make down      # remove containers, the data volumes and the built test client image
```

`make failover`: stop the primary → view change, transfer 501 commits on the other two; restart
it, stop another replica → transfer 502 (reverses 501) commits via the old primary; restart
everything.

## Setup

Cluster `0`, as in the [Docker Compose recipe](https://docs.tigerbeetle.com/operating/deploying/docker/#run-a-multi-node-cluster-using-docker-compose):
each replica formats its own data file (`--replica=N --replica-count=3`) on first start, then runs
`tigerbeetle start --addresses=<all three replicas>`. Every commit goes to a quorum of 2; the
cluster survives one failed replica. The docs recommend
[6 replicas on separate machines](https://docs.tigerbeetle.com/operating/cluster/) for production.

| replica | address | host port |
|---------|---------|-----------|
| `tigerbeetle-0` | `10.203.53.10:3000` | `3033` (`TIGERBEETLE_PORT_0`) |
| `tigerbeetle-1` | `10.203.53.11:3000` | `3034` (`TIGERBEETLE_PORT_1`) |
| `tigerbeetle-2` | `10.203.53.12:3000` | `3035` (`TIGERBEETLE_PORT_2`) |

- A host client passes `127.0.0.1:3033,127.0.0.1:3034,127.0.0.1:3035`; the order must match the
  replica indexes.
- `--addresses` takes IPs only, identical and in replica order on every replica and client. The
  docs use `network_mode: host`; this example uses static IPs on a bridge network
  (`10.203.53.0/24`, network `tigerbeetle`), which also works on Docker Desktop.
- The primary of view `v` is replica `v mod 3`; `make status` derives it from the
  `transition_to_normal … view=` log lines.
- Image `ghcr.io/tigerbeetle/tigerbeetle:0.17.9` (`TIGERBEETLE_VERSION`); test client
  `python:3.13-slim` + `pip install tigerbeetle==0.17.9`. Server and client need io_uring
  (`security_opt: seccomp=unconfined`); `cap_add: IPC_LOCK` allows memory locking.
- `--cache-grid=256MiB` per replica; each still allocates ~2.3 GiB (~7 GiB in total).

## Benchmark

`make benchmark` runs `tigerbeetle benchmark` ([`bench/run.sh`](bench/run.sh)) in a container on
the cluster network against all three replicas, then 100 `get_account_transfers` queries.

| variable | default | meaning |
|----------|---------|---------|
| `ACCOUNTS` | 10,000 | accounts created |
| `TRANSFERS` | 1,000,000 (`SMOKE=1`: 100,000) | transfers between random account pairs |
| `CLIENTS` | 1 | concurrent clients |
| `BATCH` | 8,189 (most one request holds) | transfers per request |

```bash
make benchmark                          # 1M transfers
make benchmark SMOKE=1                  # 100k transfers
make benchmark TRANSFERS=5000000 CLIENTS=4 BATCH=1000
```

- Each request is one consensus round (prepare to the backups, 2 of 3 acks, commit) and one pass
  of the state machine for the whole batch, so thousands of transfers share one network round
  trip and one disk write. Reported latency is per batch. [`../single-node`](../single-node) has
  the same `make benchmark`, showing what quorum replication costs.
- Output: summary table (transfers/s = the tool's "load accepted", batch latency p50/p99/p100,
  query latency); raw output and JSON (version, parameters, Docker VM CPUs/memory, applied
  `limits`) in `results/tigerbeetle-cluster-<UTC time>.{txt,json}` (gitignored), written by
  [`bench/report.py`](bench/report.py) (standard library, `uv run --frozen` in
  `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`).
- Benchmark accounts are on ledger 2 with time-based ids, so they never collide with
  `client/demo.py` (ledger 1, ids 1-3); `make test` still passes afterwards.
- Resource caps: [`bench/limits.sh`](bench/limits.sh) gives the three replicas `BENCH_CPUS=6` /
  `BENCH_MEM=12g` in total, 2 CPUs / 4 GB each (no swap), with `docker update` and restores the
  old limits afterwards. Docker cannot remove a memory limit from a running container, so
  "unlimited" goes back as the Docker VM's total memory; `make down && make up` starts clean. The
  bench client has `cpus: 2` (`BENCH_CLIENT_CPUS`). A replica allocates ~2.3 GiB up front and runs
  its state machine on one core, so the extra CPUs mostly go to I/O.
- All replicas and the client share one Docker VM and disk: this measures the example, not
  TigerBeetle on dedicated machines.

### Sample results

2026-09-28, defaults, Docker Desktop 29.5.3, Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64,
native image), TigerBeetle 0.17.9, 6 CPUs / 12 GB in total (2 CPUs / 4 GB per replica), client 2
CPUs.

| setup | transfers | transfers/s | batch p50 ms | batch p99 ms | lookup p99 ms |
|-------|----------:|------------:|-------------:|-------------:|--------------:|
| 3 replicas (this example) | 1,000,000 | 380,543 | 14 | 50 | 58 |
| 1 replica ([single-node](../single-node)) | 1,000,000 | 689,128 | 6 | 26 | 25 |

`lookup` is `get_account_transfers`. Quorum commit costs ~45% of throughput and roughly doubles
batch latency. Each run takes 1.5 to 2.6 s; repeat it or raise `TRANSFERS` before comparing small
differences.
