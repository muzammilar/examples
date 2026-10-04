# Tarantool — wallet transfers (stored procedure vs client transaction vs Valkey Lua)

In-memory OLTP with the business logic next to the data: a wallet transfer (idempotency check,
balance check, two balance updates, a history row) as one call of a Lua stored procedure,
compared with the same logic done by the client in an interactive transaction (8 round trips),
and with a Valkey Lua script ([`../../valkey/minimal-lua`](../../valkey/minimal-lua) is the
repo's Valkey example). Go client: [go-tarantool v3](https://github.com/tarantool/go-tarantool)
(v3.0.2, the current major; v2 is the previous one) and [rueidis](https://github.com/redis/rueidis).

## Quick start

```bash
make up      # Tarantool 3.8.1 + Valkey 9.1.2, 2 CPUs / 4 GB each
make run     # build client/ and run the three phases; exit 1 if an audit fails
make status  # containers, Tarantool audit()
make cli     # tt connect
make down    # remove containers, volumes and the built client image
```

## Setup

| service | image | port | limits | role |
|---|---|---|---|---|
| `tarantool` | `tarantool/tarantool:3.8.1` | `127.0.0.1:3331` | 2 CPUs, 4 GB | [`app/config.yaml`](app/config.yaml), [`app/init.lua`](app/init.lua): `accounts`, `transfers` (memtx), `transfer()`, `reset()`, `audit()`; memtx MVCC on (needed by the client-txn phase); `wal.mode: write` |
| `valkey` | `valkey/valkey:9.1.2-alpine` | `127.0.0.1:6391` | 2 CPUs, 4 GB | `--appendonly yes --appendfsync everysec --io-threads 2`; [`client/transfer.lua`](client/transfer.lua), [`client/audit.lua`](client/audit.lua) |
| `client` (profile `run`) | built from [`client/Dockerfile`](client/Dockerfile) (`golang:1.26-alpine` → `alpine:3.22`) | – | 4 CPUs | [`client/main.go`](client/main.go) |

Settings (env vars of `make run`):

| variable | default | meaning |
|---|---|---|
| `ACCOUNTS` | 100,000 | accounts, 1,000 each |
| `TRANSFERS` | 200,000 | transfers per phase, amount 1–100, same seeded stream in every phase |
| `HOT_PCT` / `HOT_ACCOUNTS` | 20 / 100 | 20% of transfers debit or credit one of 100 hot accounts |
| `WORKERS` | 64 | goroutines; one connection per backend (requests are multiplexed) |
| `PHASES` | all three | subset, e.g. `PHASES=tarantool-procedure` |

## What it does

| phase | one transfer is |
|---|---|
| `tarantool-procedure` | `CALL transfer(req_id, from, to, amount)`: 1 round trip; `box.atomic` (read-committed) on the server |
| `tarantool-client-txn` | IPROTO stream: `BEGIN`, `SELECT transfers[req_id]`, `SELECT` from, `SELECT` to, `UPDATE`, `UPDATE`, `INSERT`, `COMMIT` = 8 round trips; on `Transaction has been aborted by conflict` the whole transaction is retried |
| `valkey-lua` | `EVALSHA transfer.lua` with keys `acct:<from>`, `acct:<to>`, `req:<req_id>`: 1 round trip |

All three return the same codes: applied, insufficient funds (rejected), duplicate request id.
After each phase the client checks: the sum of balances is still 100,000,000; no balance is
negative; stored transfers = applied transfers; replaying the first 1,000 request ids changes
nothing. A failure exits 1.

## Results

2026-10-04, Apple M4 Pro, Docker VM aarch64 (11 CPUs, shared with other agents), Tarantool 3.8.1,
Valkey 9.1.2, each server 2 CPUs / 4 GB, client 4 CPUs, 64 workers, one run:

| phase | tx/s | p50 ms | p99 ms | max ms | applied | rejected | retries | audit |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `tarantool-procedure` | 65,470 | 0.825 | 2.736 | 11.5 | 199,642 | 358 | 0 | ok |
| `tarantool-client-txn` | 37,196 | 1.603 | 3.969 | 21.1 | 199,641 | 359 | 3,592 | ok |
| `valkey-lua` | 149,866 | 0.379 | 1.701 | 16.1 | 199,641 | 359 | 0 | ok |

Other single runs of `tarantool-procedure` the same day: 56,310 and 56,531 tx/s (with MVCC,
WAL `write`), 60,809 (MVCC off), 70,850 (`wal.mode: none`).

- The stored procedure was 1.76x the client-side transaction and had no conflicts; the client
  transaction needed 3,592 retries (1.8% of transfers).
- 8 round trips cost less than 8x because 64 goroutines pipeline their requests over one
  connection; with fewer workers or a real network the gap grows with the RTT.
- Valkey ran the same logic ~2.3x faster than Tarantool here. Turning off MVCC or the WAL
  raised Tarantool to 61k / 71k tx/s, so most of the time is in the TX thread running the Lua
  procedure and its five box operations, not in the WAL.
- The counts differ by 1 between phases: with 64 workers the order of transfers on an account
  varies, so a different transfer hits insufficient funds.

## Design notes

- One TX thread runs every request and Lua procedure; a procedure that does not yield (memtx
  only) runs to the end without interleaving, so `transfer()` cannot see a half-done transfer.
- `box.atomic({ txn_isolation = 'read-committed' }, …)`: with MVCC on, the default `best-effort`
  isolation aborted read-then-write transactions that overlapped a WAL write
  ([`../single-node`](../single-node/README.md#known-issues)). Read-committed sees changes that
  are committed but still waiting for the WAL.
- The request id is the primary key of `transfers`, so a retried request is a no-op
  (`DUPLICATE`) instead of a second debit. The Valkey script uses a `req:<id>` key for the same.
- Differences that the numbers do not show: an error inside `box.atomic` rolls back every change
  made so far; a Valkey script that fails half-way keeps the writes it already made (Valkey
  scripts are atomic, not transactional). Tarantool has secondary indexes and WAL + snapshots
  with per-commit `write()`; the Valkey side here uses AOF with `fsync` once a second.
- In the client-txn phase each stream's transaction holds MVCC read sets across round trips;
  conflicts are detected at write/commit time and the client retries the whole transaction.

## Known issues

- `go-tarantool` v3 is current (v3.0.2, 2026-09-10); v2 code needs the
  [migration guide](https://github.com/tarantool/go-tarantool/blob/master/MIGRATION.md). The
  stream API (`NewStream`, `NewBeginRequest().TxnIsolation(…)`) works with Tarantool 3.8.1.
- A first run failed in the client-txn phase with `Failed to write to disk (ClientError, code
  0x28)`: the shared Docker VM's disk was full. Tarantool rejects the commit (`WAL_IO`) and keeps
  running.

## Links

- Stored procedures / Lua: https://www.tarantool.io/en/doc/latest/platform/app/
- Interactive transactions over streams: https://www.tarantool.io/en/doc/latest/platform/atomic/txn_mode_mvcc/
- go-tarantool: https://pkg.go.dev/github.com/tarantool/go-tarantool/v3
