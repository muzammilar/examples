# TigerBeetle — single replica

One TigerBeetle replica following the [Docker recipe](https://docs.tigerbeetle.com/operating/deploying/docker/):
a one-shot `format` service runs `tigerbeetle format --cluster=0 --replica=0 --replica-count=1`
into a named volume, then `tigerbeetle start --addresses=0.0.0.0:3000` serves it.
Client port: `localhost:3033` (override with `TIGERBEETLE_PORT`).

```bash
make up       # format the data file (first run only) and start the replica
make test     # client/demo.py via the Python client: create_accounts, a deposit, two-phase
              # pending -> post / void, a linked chain that fails as a whole, a transfer rejected
              # by debits_must_not_exceed_credits, lookup_accounts; then the REPL
make status   # container state, version, balances through the REPL
make cli      # interactive tigerbeetle repl
make down     # remove the container and the data volume
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
