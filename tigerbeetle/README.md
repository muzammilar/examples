# TigerBeetle

Website: https://tigerbeetle.com/

`single-node` and `docker-compose-cluster` run `client/demo.py` with the official Python client
(accounts, transfers, two-phase pending → post/void, a linked chain that fails atomically, a
transfer rejected by `debits_must_not_exceed_credits`) and use the built-in `tigerbeetle repl`.

- [`single-node/`](single-node) — one replica (`--replica-count=1`) on Docker Compose.
- [`docker-compose-cluster/`](docker-compose-cluster) — three replicas of one cluster, with a `make failover` that stops the primary.
- [`ledger-showcase/`](ledger-showcase) — a wallet/payments ledger in Rust (official client, built from source): 1M linked payment + fee transfers with overdraft protection, card holds (post/void/expire), linked currency exchange, idempotent retries, and an audit that debits equal credits.

## Benchmark

`tigerbeetle benchmark`, 1M transfers (Apple M4 Pro, Docker VM aarch64, 2026-09-28): one replica
(4 CPUs / 6 GB) does 689k transfers/s at 26 ms batch p99; three replicas (2 CPUs / 4 GB each) do
381k/s at 50 ms p99 — quorum commit costs ~45% of throughput. Full tables and method:
[`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark) and
[`single-node/README.md`](single-node/README.md#benchmark).
