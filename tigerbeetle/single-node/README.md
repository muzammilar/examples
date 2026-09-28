# TigerBeetle — single replica

One TigerBeetle replica following the [Docker recipe](https://docs.tigerbeetle.com/operating/deploying/docker/):
a one-shot `format` service runs `tigerbeetle format --cluster=0 --replica=0 --replica-count=1`
into a named volume, then `tigerbeetle start --addresses=0.0.0.0:3000` serves it.
Client port: `localhost:3033` (override with `TIGERBEETLE_PORT`).

```bash
make up        # format the data file (first run only) and start the replica
make test      # client/demo.py via the Python client: create_accounts, a deposit, two-phase
               # pending -> post / void, a linked chain that fails as a whole, a transfer rejected
               # by debits_must_not_exceed_credits, lookup_accounts; then the REPL
make benchmark # tigerbeetle benchmark against the replica: 1M transfers (SMOKE=1: 100k)
make status    # container state, version, balances through the REPL
make cli       # interactive tigerbeetle repl
make down      # remove the containers, the data volume and the built test client image
```

- Image `ghcr.io/tigerbeetle/tigerbeetle:0.17.9` (override with `TIGERBEETLE_VERSION`); the
  test client is `python:3.13-slim` + `pip install tigerbeetle==0.17.9`, built by `make test`.
- TigerBeetle (server *and* client library) needs io_uring, which Docker 25+ blocks by default,
  so both run with `security_opt: seccomp=unconfined`; `cap_add: IPC_LOCK` lets it lock memory
  (the docs' fix for `error: SystemResources` on macOS). Works on Docker Desktop (arm64).
- `--cache-grid=256MiB` shrinks the 1 GiB default grid cache; the replica still allocates ~2.3 GiB.
- Clients take IP addresses only (no hostnames), so the test client shares the replica's network
  namespace and connects to `127.0.0.1:3000`.
- The demo uses fixed IDs, so re-running `make test` is idempotent (`EXISTS`, balances unchanged).
  Cluster ID `0` is reserved for testing; the docs recommend a random 128-bit ID in production.

## Benchmark

`make benchmark` runs TigerBeetle's own load generator, `tigerbeetle benchmark`
([`bench/run.sh`](bench/run.sh)), in a container sharing the replica's network namespace. It
creates `ACCOUNTS` accounts (default 10,000) and commits `TRANSFERS` transfers between random
pairs of them (default 1,000,000; `SMOKE=1`: 100,000) from `CLIENTS` clients (1), each request a
batch of up to `BATCH` transfers (8,189, the most one request holds), then runs 100
`get_account_transfers` queries.

What it shows: TigerBeetle's throughput comes from batching. Every request is one pass of the
double-entry state machine and one write to the log for the whole batch, so thousands of transfers
share the cost of one round trip; the latency it reports is per batch, not per transfer. With one
replica nothing is replicated; the 3-replica example
([`../docker-compose-cluster`](../docker-compose-cluster)) runs the same benchmark against a
quorum.

```bash
make benchmark                          # 1M transfers
make benchmark SMOKE=1                  # 100k transfers
make benchmark TRANSFERS=5000000 CLIENTS=4 BATCH=1000
```

It prints a summary table (transfers/s = the tool's "load accepted", batch latency p50/p99/p100,
query latency) and keeps the raw output plus parsed JSON with the version, parameters and Docker
VM CPUs/memory in `results/tigerbeetle-single-<UTC time>.{txt,json}` (gitignored), written by
[`bench/report.py`](bench/report.py) (standard library, `uv run --frozen` in
`ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`). The benchmark's accounts live on ledger 2
with time-based ids, so they never collide with `client/demo.py` (ledger 1, ids 1-3) and
`make test` still passes afterwards.

### Sample results

TODO: numbers from a quiet machine.

| setup | transfers | transfers/s | batch p50 ms | batch p99 ms |
|-------|----------:|------------:|-------------:|-------------:|
| 1 replica (this example) | 1,000,000 | TODO | TODO | TODO |
| 3 replicas ([docker-compose-cluster](../docker-compose-cluster)) | 1,000,000 | TODO | TODO | TODO |
