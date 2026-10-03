# TigerBeetle — payments ledger (Rust client)

A wallet/payments ledger on one TigerBeetle replica, driven by a Rust program ([`app/`](app))
using the official client. Every money movement is a double-entry transfer; every rule (no
overdraft, holds, atomic multi-leg moves, idempotency) is enforced by the database.

## Quick start

```bash
make up      # format the data file (first run only) and start the replica
make run     # build the app image (first time: a few minutes) and run it
make build   # build the app image only
make status  # container state and server version
make down    # remove the containers, the data volume and the built image
```

Client port `localhost:3034` (`TIGERBEETLE_PORT`). Sizes: `USERS`, `MERCHANTS`, `PAYMENTS`,
`CLIENTS`, `HOLDS`, `EXCHANGES`, e.g. `make run PAYMENTS=2000000 CLIENTS=4` (passed to compose).

## What `make run` does

All IDs are fresh `tb::id()`s, so it can be re-run on the same cluster.

| step | what |
|------|------|
| 1. accounts | USD and EUR issuers, a fee account, FX liquidity accounts, 100 merchants, a USD + EUR wallet for each of 10,000 users. Wallets, merchants and EUR liquidity have `debits_must_not_exceed_credits`. |
| 2. funding | $500 to every wallet, EUR 50,000 of exchange liquidity. |
| 3. payment storm | 500,000 payments from 2 concurrent clients, requests of 8,188 events. Each payment is a *linked* pair (payer → merchant or user, payer → fee account, 1%): both commit or neither. Random amounts overspend, so ~40% are rejected with `exceeds credits`; every fee lands on one hot account. Then a payday ($500 each). |
| 4. holds | Two-phase transfers as card authorizations. Alice has $100: $80 hold created, $30 payment fails (holds count against the balance), hold voided, same payment succeeds. Then 30,000 holds: a third captured at 80% (rest released), a third voided, a third left to expire (1 s timeout); posting an expired hold returns `pending transfer expired`. |
| 5. currency exchange | 20,000 USD → EUR exchanges, each a linked USD leg (wallet → USD liquidity) and EUR leg (EUR liquidity → EUR wallet). When EUR liquidity runs out, the EUR leg fails and the USD leg rolls back with it (`linked event failed`). |
| 6. idempotency | Re-sends a payment batch with the same IDs: successful payments answer `exists` (their fee leg `linked event failed`: `exists` ends the chain), rejected ones `id already failed`. Nothing moves twice. |
| 7. audit | Looks up all 20,108 accounts and compares each with a model built only from `created` replies; per ledger, sum(debits) = sum(credits) (posted and pending), no limited account has debits above credits. Exits non-zero otherwise. |

## The Rust client

The client lives in the main repo ([`src/clients/rust`](https://github.com/tigerbeetle/tigerbeetle/tree/0.17.9/src/clients/rust)),
is not on crates.io (the `tigerbeetle` crate there is a 0.0.1 placeholder), and links the native
`tb_client` library that `zig build clients:rust` produces. [`app/Dockerfile`](app/Dockerfile):

1. clones tag `0.17.9`;
2. builds the library with the repo's pinned Zig, stamped with
   `-Dconfig-release=0.17.9 -Dconfig-release-client-min=0.16.4` (a plain source build reports a
   dev release that the server rejects);
3. `cargo build`s the app against the crate by path (`/tigerbeetle/src/clients/rust` in
   [`Cargo.toml`](app/Cargo.toml));
4. final image: `debian:bookworm-slim` plus the binary. No host Rust or Zig needed.

- Server image `ghcr.io/tigerbeetle/tigerbeetle:0.17.9` (`TIGERBEETLE_VERSION`; build arg
  `TIGERBEETLE_CLIENT_MIN` if the minimum changes, shown by `tigerbeetle version --verbose`).
- Same Docker settings as [`../single-node`](../single-node): `seccomp=unconfined` for io_uring
  (server and client), `IPC_LOCK`, `--cache-grid=256MiB`, client shares the replica's network
  namespace (IP addresses only).
- Compose project and container `tigerbeetle-ledger`, so it runs next to the other TigerBeetle
  examples.

## Sample output

2026-10-02, fresh `make up` then `make run` (defaults), Docker Desktop 29.5.3, Apple M4 Pro
(Docker VM: 11 CPUs, 24.4 GB, aarch64), TigerBeetle 0.17.9, no CPU or memory caps:

```text
1. accounts        20108 created in 661 ms; wallets, merchants and EUR liquidity have debits_must_not_exceed_credits
2. funding         $500.00 each to 10000 wallets; 50000.00 EUR liquidity
3. payment storm   500000 payments = 1000000 transfers (payment + fee, linked) in 1.69 s = 592733 transfers/s, 124 requests of up to 8188 events, batch p50 20.9 ms / p99 92.5 ms
                   payments: created: 297865, exceeds credits: 202135
                   fees collected $68222.67 on one hot account; every wallet overspend rejected
                   payday: $500.00 to every wallet again
4. holds           alice has $100.00, hold $80.00 -> created; pay $30.00 -> exceeds credits; void hold -> created; pay $30.00 -> created
                   30000 holds: created: 27561, exceeds credits: 2439; 9179 captured at 80% ($721314.16), 9179 voided, 9203 expired after 1.0 s; capturing an expired hold -> pending transfer expired
5. exchange        20000 USD->EUR at 0.92: created: 986, exceeds credits: 19014; 19013 USD legs rolled back because EUR liquidity ran out (left: EUR 3.35)
6. idempotency     re-sent a storm batch (532 transfers): exists: 19, id already failed: 247, linked event failed: 266
7. audit           20108 accounts looked up
                   ledger 840: debits 17813588.84 == credits 17813588.84 (ok), pending 0 / 0
                   ledger 978: debits 99996.65 == credits 99996.65 (ok), pending 0 / 0
                   0 accounts differ from the client-side model, 0 limited accounts below zero, money in circulation $10000100.00

AUDIT PASSED
```

Storm throughput, 1M transfers, fresh cluster each run (`make down && make up`):

| clients | transfers/s | batch p50 ms | batch p99 ms |
|--------:|------------:|-------------:|-------------:|
| 1 | 480,689 | 11.7 | 44.0 |
| 2 (default) | 592,733 / 533,414 / 857,587 | 20.9 / 24.8 / 16.9 | 92.5 / 61.0 / 39.4 |
| 4 | 465,211 | 49.5 | 266.4 |

- The third 2-client run is a 2026-10-03 retest on the same machine (identical counts, audit
  passed); the spread shows how noisy a ~1–2 s storm is.
- One replica executes requests one at a time: a second client only overlaps round trips; more
  just queue.
- Batch latency is per request of 8,188 transfers. Each client registers its session with one
  lookup before the clock starts.
- TigerBeetle's own load generator on the same machine:
  [`../single-node`](../single-node/README.md#benchmark).

## Known issues

- Re-running on the same cluster gets slower as the data file grows (after the sample run: 675k
  and 545k/s, then 204k/s with 1 client and 174k/s with 4). `make down && make up` restores
  first-run numbers.
- Rust client not on crates.io; see [The Rust client](#the-rust-client).

## Design notes

- **Batching with per-event results.** One request carries 8,188 transfers, runs as one
  state-machine pass and one log write, and returns a status per transfer. 1M balance-checked
  double-entry transfers took under 2 s on a laptop.
- **Invariants in the database.** No overdraft (`debits_must_not_exceed_credits`), holds,
  partial capture, void, timeouts and atomic multi-leg chains are built-in transfer types: no
  application locks or read-check-write.
- **Hot accounts cost nothing extra.** Every payment touches the one fee account. In a SQL ledger
  that is a row lock per transaction; here transfers are applied in order inside the batch.
- **Idempotency by ID.** A retried request can't double-charge: the same ID returns `exists` or
  `id already failed`.
- **Auditable.** Debits equal credits per ledger by design; the audit only confirms the replies
  matched what was stored.
