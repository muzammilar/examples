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

Ledger showcase (Rust client, one replica, no CPU limits, 2026-10-02): 1M payment + fee
transfers in linked pairs, every fee to one hot account, ran at 481k transfers/s from 1 client
(batch p50 11.7 / p99 44 ms), 533–593k/s from 2 and 465k/s from 4, with overdrafts rejected by the
database and an audit that found 0 mismatches. Rough numbers: each run lasts ~2 s. Details:
[`ledger-showcase/README.md`](ledger-showcase/README.md#sample-output).


## Known issues

Seen while building these examples (TigerBeetle 0.17.9, 2026-10-02):

- The official Rust client is not on crates.io: the `tigerbeetle` crate there is a 0.0.1
  placeholder from 2023. The real client lives in the main repo (`src/clients/rust`) and links a
  native `tb_client` library that must be built with Zig from a release tag, with
  `-Dconfig-release` / `-Dconfig-release-client-min` matching the server, or the server rejects
  the client. `ledger-showcase/showcase/Dockerfile` does this.
- Re-running `ledger-showcase` against the same data file gets slower: ~675k/s and ~545k/s on the
  2nd and 3rd runs, ~200k/s and ~174k/s on the 4th and 5th. A fresh cluster (`make down up`)
  restores the first-run numbers.
