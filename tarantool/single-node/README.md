# Tarantool — single node

One Tarantool 3.8.1 instance on Docker Compose: memtx and vinyl spaces, TREE/HASH indexes,
Lua stored procedures, transactions (server-side and over an IPROTO stream), and a restart after
`SIGKILL` that recovers from snapshot + WAL.

## Quick start

```bash
make up         # start the instance, wait for the healthcheck
make test       # scripts/01-04, then `make restart`
make restart    # write rows, box.snapshot(), write more, SIGKILL, start, check every row is back
make benchmark  # bench/bench.lua: memtx/vinyl replace + get, transfer() calls (2 CPUs / 2 GB)
make status     # container state, box.info version/status/ro/lsn
make cli        # tt connect (Lua console)
make down       # remove the container, the data volume and the network
```

## Setup

| service | image | port | role |
|---|---|---|---|
| `tarantool` | `tarantool/tarantool:3.8.1` (arm64 native) | `127.0.0.1:3301` (`TARANTOOL_PORT`) | the instance |
| `client` (profile `test`) | same | – | `scripts/run.lua`: evals each script on the server via `net.box` |
| `bench` (profile `bench`) | same, `cpus: 2` | – | `bench/bench.lua` |

- [`app/config.yaml`](app/config.yaml): Tarantool 3 declarative config. The image's entrypoint
  loads `/opt/tarantool/$TT_APP_NAME/config.yaml` and starts `$TT_INSTANCE_NAME`
  (`single` / `instance-001`). User `app` / `secret` (role `super`).
- `memtx.memory` 1 GiB, `vinyl.memory` / `vinyl.cache` 128 MiB, `database.use_mvcc_engine: true`
  (needed for interactive transactions over streams), `wal.mode: write`, `snapshot.count: 2`,
  hourly checkpoint.
- [`app/init.lua`](app/init.lua) (`app.file`): creates the schema once (`box.once`) and defines the
  stored procedures on every start (Lua functions are not persisted).
- `app.file` is resolved against the working directory, so the service sets
  `working_dir: /opt/tarantool/single`.
- Data (snapshots, WALs, vinyl runs) in volume `tarantool-data` at
  `/var/lib/tarantool/sys_env/single/instance-001`.

## What it does

| script | shows |
|---|---|
| [`01-spaces.lua`](scripts/01-spaces.lua) | spaces, engines, indexes: `accounts` (memtx, TREE pk + non-unique TREE on `owner`), `sessions` (memtx HASH), `transfers` (memtx), `transfers_archive` (vinyl, LSM on disk), `counters` |
| [`02-crud.lua`](scripts/02-crud.lua) | 1,000 accounts in one `box.atomic`, `get`, secondary-index `select`, `GE` range, HASH `get`, `upsert` twice (insert, then `+5`) |
| [`03-procedures.lua`](scripts/03-procedures.lua) | `transfer()` x10; rejected calls (`insufficient funds`, `no such account`) change nothing; `total_balance()` unchanged; `archive(8)` moves 8 transfers memtx → vinyl; `history()` reads both |
| [`04-transactions.lua`](scripts/04-transactions.lua) | `box.begin/rollback`, savepoint, error inside `box.atomic`; a net.box stream transaction: another connection reads the old balance (1000) until commit (1025) |
| [`05-before-restart.lua`](scripts/05-before-restart.lua) / [`06-after-restart.lua`](scripts/06-after-restart.lua) | 10,000 rows in a memtx and a vinyl space, `box.snapshot()`, 5,000 more (WAL only), `docker kill -s KILL`, start, all 15,000 rows and the account totals are back |

Recovery log after the kill: `recovering from …21074.snap`, `recover from …21074.xlog`,
``file `…21074.xlog` wasn't correctly closed``, then `ready to accept requests` (~10 ms of
recovery for 15k rows).

Stored procedures ([`app/init.lua`](app/init.lua)):

| function | does |
|---|---|
| `transfer(from, to, amount)` | in one `box.atomic`: read both accounts, check funds, two `update`s, bump a counter, insert the transfer; `box.error` rolls back |
| `archive(up_to, batch)` | move transfers with `id <= up_to` to `transfers_archive` (vinyl), `batch` rows per transaction |
| `history(account, n)` | last `n` transfers out of an account: memtx, then the vinyl archive (`REQ` on `from_ts`) |
| `total_balance()` | sum of all balances (invariant: 1,000,000 after `02-crud`) |

## Benchmark

[`bench/bench.lua`](bench/bench.lua) runs in the tarantool image (no other tools): `FIBERS=64`
fibers over `CONNS=4` net.box connections (net.box pipelines concurrent requests on one
socket), each op for `DURATION=10` s on `KEYS=100000` random keys, 100-byte payload. It ends by
checking `total_balance()` is unchanged. [`bench/limits.sh`](bench/limits.sh) caps the
instance at `BENCH_CPUS=2` / `BENCH_MEM=2g` with `docker update` and restores afterwards; the
bench client has 2 CPUs. Raw output: `results/tarantool-single-<UTC time>.txt` (gitignored).

```bash
make benchmark
make benchmark DURATION=30 FIBERS=128 OPS=memtx_get,call_transfer
```

2026-10-04, Apple M4 Pro, Docker VM aarch64 (Docker 29.5.3, 11 CPUs, 24.4 GB, shared with other
agents' containers), Tarantool 3.8.1, 2 CPUs / 2 GB for the instance, one 10 s run per op:

| op | ops/s | p50 ms | p99 ms | p99.9 ms | max ms | errors |
|---|---:|---:|---:|---:|---:|---:|
| `memtx_replace` | 230,559 | 0.244 | 0.782 | 2.122 | 172.8 | 0 |
| `memtx_get` | 362,387 | 0.144 | 0.521 | 4.550 | 275.3 | 0 |
| `vinyl_replace` | 93,663 | 0.307 | 1.695 | 70.491 | 81.0 | 0 |
| `vinyl_get` | 234,779 | 0.198 | 1.438 | 4.242 | 81.3 | 0 |
| `call_transfer` (`transfer()`, 1 call = 1 transaction) | 62,645 | 0.784 | 3.625 | 5.710 | 11.0 | 0 |

- Ratios: memtx get / replace 1.57x; memtx / vinyl replace 2.46x; memtx replace / `transfer()`
  3.68x (one `transfer()` = 2 reads, 3 updates, 1 insert, 1 WAL write).
- `vinyl_replace` p99.9 70.5 ms vs 2.1 ms for `memtx_replace` (cause not investigated).
- Total balance before/after: 1,000,000 / 1,000,000.
- [`go/lua-bench/tarantool`](../../go/lua-bench/tarantool) runs the
  [`go/rueidis-lua-bench`](../../go/rueidis-lua-bench) workload (put/get + add/update/delete as
  Lua stored functions) against this instance, for comparison with Valkey and Dragonfly:
  put 175,238, get 292,625, `add` 198,419, `update` 111,169, `delete` 187,135 ops/s
  (`-n 1000000 -c 50 -keys 100000`, 2 CPUs / 2 GB, 2026-10-04).

## Known issues

- **MVCC conflicts in `transfer()` with the default isolation.** With
  `database.use_mvcc_engine: true`, the first version of `transfer()` used plain `box.atomic(fn)`
  (isolation `best-effort`). Under 64 concurrent callers, 56,902 of ~690k calls in 10 s failed
  with `Transaction has been aborted by conflict` (63,611 ok/s). The transaction starts with
  reads, so it reads only confirmed data, while the previous transfer is still waiting for its
  WAL write; its update then conflicts. `box.atomic({ txn_isolation = 'read-committed' }, fn)`
  reads prepared changes: 0 conflicts, 71,426 ok/s in a 5 s check. Without MVCC (the default
  `false`) memtx transactions cannot yield and run strictly one after another.
- **memtx + vinyl in one hot transaction.** An earlier `transfer()` also inserted into a vinyl
  space. A vinyl statement may read from disk and yield, so concurrent transfers overlapped and
  aborted (`Transaction has been aborted by conflict`, 111,457 aborted vs 22,901 ok/s in a 5 s
  run, default isolation). `transfer()` now writes memtx only; `archive()` moves rows to vinyl in
  separate batches.
- **`box.sequence:next()` then a vinyl statement in one transaction fails:** `Vinyl does not
  support executing a statement in a transaction that is not allowed to yield` (the sequence
  write goes to a system memtx space). The other order works. Not hit in the current code.
- **Client scripts in the image need `env -u TT_INSTANCE_NAME`.** The image sets
  `TT_INSTANCE_NAME=instance-001`; a plain `tarantool script.lua` then looks for a cluster config
  and fails with `No cluster config received from the given configuration sources`. Setting it
  to `""` fails with `[--name] Zero length name is forbidden`.
- `app.file` is relative to the process's working directory, not the config file
  (`cannot open init.lua: No such file or directory` with the image's default
  `/opt/tarantool`).
- After recovery the log warns `snapshot recovery performance is better with new value of
  compat.box_recovery_triggers_deprecation`; the default is kept here.

## Links

- Configuration reference: https://www.tarantool.io/en/doc/latest/reference/configuration/configuration_reference/
- Transactions, MVCC and isolation levels: https://www.tarantool.io/en/doc/latest/platform/atomic/txn_mode_mvcc/
- Storage engines (memtx, vinyl): https://www.tarantool.io/en/doc/latest/platform/engines/
- Docker image: https://hub.docker.com/r/tarantool/tarantool
