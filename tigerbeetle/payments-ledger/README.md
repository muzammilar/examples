# TigerBeetle — payments ledger (Rust client)

A wallet/payments ledger on one TigerBeetle replica, driven by a Rust program using the official client.

## Quick start

```bash
make up      # format the data file (first run only) and start the replica
make run     # build the app image (first time: a few minutes) and run it
make status  # container state and server version
make down    # remove the containers, the data volume and the built image
```

The program is [`app/`](app) (official Rust client). Every money movement is a double-entry transfer and every
rule (no overdraft, holds, atomic multi-leg moves, idempotency) is enforced by the database, not
the application. Client port: `localhost:3034` (override with `TIGERBEETLE_PORT`).

What `make run` does (all IDs are fresh `tb::id()`s, so it can be re-run on the same cluster):

1. **accounts** — USD and EUR issuers, a fee account, FX liquidity accounts, 100 merchants and a
   USD + EUR wallet for each of 10,000 users. Wallets, merchants and the EUR liquidity account have
   `debits_must_not_exceed_credits`: the database refuses anything that would make them negative.
2. **funding** — $500 to every wallet, EUR 50,000 of exchange liquidity.
3. **payment storm** — 500,000 payments from 2 concurrent clients, in requests of 8,188 events.
   Each payment is a *linked* pair (payer → merchant or user, payer → fee account, 1%): both legs
   commit or neither does. Random amounts make users overspend, so about 40% are rejected with
   `exceeds credits`, under contention, with every payment's fee landing on one hot account.
   Then a payday (another $500 each).
4. **holds** — two-phase transfers as card authorizations. Alice has $100: a $80 hold is
   created, a $30 payment then fails (holds count against the balance), the hold is voided, the
   same payment succeeds. Then 30,000 holds: a third captured for 80% of the hold (the rest is
   released), a third voided, a third with a 1 s timeout left to expire; posting an expired hold
   returns `pending transfer expired`.
5. **currency exchange** — 20,000 USD → EUR exchanges, each a linked USD leg (wallet → USD
   liquidity) and EUR leg (EUR liquidity → EUR wallet). When the EUR liquidity runs out, the EUR
   leg fails and the USD leg is rolled back with it (`linked event failed`).
6. **idempotency** — re-sends a payment batch with the same transfer IDs: payments that went
   through answer `exists` (their fee leg `linked event failed`: `exists` ends the chain), ones that
   were rejected answer `id already failed`. Nothing moves twice.
7. **audit** — looks up all 20,108 accounts and compares each with a model built only from the
   `created` replies; per ledger, sum(debits) must equal sum(credits) (posted and pending), and no
   limited account may have debits above credits. Exits non-zero otherwise.

Override the sizes with `USERS`, `MERCHANTS`, `PAYMENTS`, `CLIENTS`, `HOLDS`, `EXCHANGES`, e.g.
`make run PAYMENTS=2000000 CLIENTS=4` (make passes them to compose).

## The Rust client

TigerBeetle's Rust client lives in the main repo ([`src/clients/rust`](https://github.com/tigerbeetle/tigerbeetle/tree/0.17.9/src/clients/rust))
but is not on crates.io yet (the `tigerbeetle` crate there is a 0.0.1 placeholder), and it links
the native `tb_client` library that `zig build clients:rust` produces. So
[`app/Dockerfile`](app/Dockerfile) clones tag `0.17.9`, builds the library with the
repo's pinned Zig, stamped with `-Dconfig-release=0.17.9 -Dconfig-release-client-min=0.16.4` (a
plain source build reports a dev release that the server would reject), then `cargo build`s the
the app against the crate by path (`/tigerbeetle/src/clients/rust` in [`Cargo.toml`](app/Cargo.toml)).
The final image is `debian:bookworm-slim` plus the binary. No host Rust or Zig needed.

- Server image `ghcr.io/tigerbeetle/tigerbeetle:0.17.9` (override with `TIGERBEETLE_VERSION`, and
  `TIGERBEETLE_CLIENT_MIN` as a build arg if the minimum changes; `tigerbeetle version --verbose`
  shows it). Same Docker settings as [`../single-node`](../single-node): `seccomp=unconfined` for
  io_uring (server and client), `IPC_LOCK`, `--cache-grid=256MiB`, and the client shares the
  replica's network namespace because it takes IP addresses only.
- Compose project `tigerbeetle-ledger`, container `tigerbeetle-ledger`, so it runs next to the
  other TigerBeetle examples.

## Sample output

2026-10-02, a fresh `make up` then `make run` (defaults), Docker Desktop 29.5.3 on an Apple M4 Pro
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

Storm throughput on a fresh cluster (each a new `make down && make up`), 1M transfers:

| clients | transfers/s | batch p50 ms | batch p99 ms |
|--------:|------------:|-------------:|-------------:|
| 1 | 480,689 | 11.7 | 44.0 |
| 2 (default) | 592,733 / 533,414 / 857,587 | 20.9 / 24.8 / 16.9 | 92.5 / 61.0 / 39.4 |
| 4 | 465,211 | 49.5 | 266.4 |

The third 2-client run is a 2026-10-03 retest on the same machine, same results otherwise
(identical counts, audit passed); the spread shows how noisy a ~1–2 s storm is.

One replica executes requests one at a time, so a second client only overlaps the round trips;
more just queue. Re-running on the same cluster gets slower as the data file grows (after the
sample run: 675k and 545k/s, then 204k/s with 1 client and 174k/s with 4), and each storm takes
about 2 s, so treat these as rough. Batch latency is per request of 8,188 transfers; each client
registers its session with one lookup before the clock starts. For TigerBeetle's own load generator on the same
machine, see [`../single-node`](../single-node/README.md#benchmark).

## Why TigerBeetle fits this

- **Batching with per-event results.** One request carries 8,188 transfers, runs as one pass of
  the state machine and one log write, and returns a status for each transfer. A million
  balance-checked, double-entry transfers took under 2 s on a laptop.
- **Invariants live in the database.** No overdraft (`debits_must_not_exceed_credits`), holds
  that reserve funds, partial capture, void, timeouts, and atomic multi-leg chains are built-in
  transfer types, so the application holds no locks and does no read-check-write.
- **Hot accounts don't serialize anything extra.** Every payment touches the one fee account. In a
  SQL ledger that is a row lock taken by every transaction; here transfers are applied in order
  inside the batch, so a hot account costs nothing beyond the transfer itself.
- **Idempotency by ID.** A retried request after a timeout can't double-charge: the same ID is
  `exists` or `id already failed`, never a second transfer.
- **Auditable by construction.** Debits equal credits per ledger by design; the audit only
  confirms the replies matched what was stored.
