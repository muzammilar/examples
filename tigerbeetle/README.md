# TigerBeetle

Website: https://tigerbeetle.com/

`single-node` and `docker-compose-cluster` run `client/demo.py` with the official Python client
(accounts, transfers, two-phase pending → post/void, a linked chain that fails atomically, a
transfer rejected by `debits_must_not_exceed_credits`) and use the built-in `tigerbeetle repl`.

- [`single-node/`](single-node) — one replica (`--replica-count=1`) on Docker Compose.
- [`docker-compose-cluster/`](docker-compose-cluster) — three replicas of one cluster, with a `make failover` that stops the primary.
- [`payments-ledger/`](payments-ledger) — a wallet/payments ledger in Rust (official client, built from source): 1M linked payment + fee transfers with overdraft protection, card holds (post/void/expire), linked currency exchange, idempotent retries, and an audit that debits equal credits.
- [`cluster-operations/`](cluster-operations) — what changes on a running cluster, each step under load: adding and removing standby replicas (3 → 3 + 2 standbys → 3), showing that standbys never form a quorum, replacing a replica's lost data file with `tigerbeetle recover`, and resizing the grid cache with a rolling restart.

## Benchmark

`tigerbeetle benchmark`, 1M transfers (Apple M4 Pro, Docker VM aarch64, 2026-09-28): one replica
(4 CPUs / 6 GB) does 689k transfers/s at 26 ms batch p99; three replicas (2 CPUs / 4 GB each) do
381k/s at 50 ms p99 — quorum commit costs ~45% of throughput. Full tables and method:
[`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark) and
[`single-node/README.md`](single-node/README.md#benchmark).

Payments ledger (Rust client, one replica, no CPU limits, 2026-10-02): 1M payment + fee
transfers in linked pairs, every fee to one hot account, ran at 481k transfers/s from 1 client
(batch p50 11.7 / p99 44 ms), 533–858k/s from 2 and 465k/s from 4, with overdrafts rejected by the
database and an audit that found 0 mismatches. Rough numbers: each run lasts ~2 s. Details:
[`payments-ledger/README.md`](payments-ledger/README.md#sample-output).

Cluster operations under load (`cluster-operations/`, 2026-10-03, one client at ~46k transfers/s):
adding two standbys, removing them, replacing a lost replica (`recover` + 4.6 s state sync) and a
rolling restart all completed without a failed request or a lost transfer. The worst single-request
stall was 1.9 s, during the rolling restart. Raising `--cache-grid` from 256 MiB to 2 GiB did not
speed up the 1M-transfer benchmark (343k → 221k transfers/s in single runs on a shared VM): its
working set already fits.

## Known issues

Seen while building these examples (TigerBeetle 0.17.9, 2026-10-02):

- The official Rust client is not on crates.io: the `tigerbeetle` crate there is a 0.0.1
  placeholder from 2023. The real client lives in the main repo (`src/clients/rust`) and links a
  native `tb_client` library that must be built with Zig from a release tag, with
  `-Dconfig-release` / `-Dconfig-release-client-min` matching the server, or the server rejects
  the client. `payments-ledger/app/Dockerfile` does this.
- Re-running `payments-ledger` against the same data file gets slower: ~675k/s and ~545k/s on the
  2nd and 3rd runs, ~200k/s and ~174k/s on the 4th and 5th. A fresh cluster (`make down up`)
  restores the first-run numbers.
- The number of active replicas is fixed at format time (`--replica-count`, at most 6). There is
  no way to go from 3 to 5 (or 6 to 3) voting replicas in place. The `reconfigure` operation in
  the protocol rejects a different replica or standby count in 0.17.9 (`src/vsr.zig`), and no
  client or CLI exposes it. Changing the replica count means a new cluster.
- Standby replicas (`format --standby=<i>`) are experimental: "standbys don't have a concrete
  practical use-case yet" (`src/tigerbeetle/cli.zig`). They cannot be promoted, and they do not
  count toward a quorum. Adding or removing one changes `--addresses`, so every replica needs a
  restart.
- A replica that lost its data file must come back with `tigerbeetle recover`, never `format`
  ([recovering](https://docs.tigerbeetle.com/operating/recovering/)).
