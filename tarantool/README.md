# Tarantool

Website: https://www.tarantool.io/ · GitHub: https://github.com/tarantool/tarantool

In-memory database and Lua application server: data in memtx (RAM, persisted by WAL +
snapshots) or vinyl (LSM on disk), business logic in Lua stored procedures next to the data.

| folder | what it shows |
|---|---|
| [`single-node/`](single-node) | One instance (Tarantool 3.8.1, YAML config): memtx/vinyl spaces, indexes, stored procedures, transactions incl. a net.box stream, recovery from snapshot + WAL after `SIGKILL`, a small net.box benchmark. |

Image: `tarantool/tarantool:3.8.1` is multi-arch (amd64 + arm64) and runs natively on Apple
Silicon. 3.x tags have shipped arm64 since 3.1 (Docker Hub, checked 2026-10-04).

## Benchmark

| example | date | setup | result |
|---|---|---|---|
| [single-node](single-node/README.md#benchmark) | 2026-10-04 | 1 instance, 2 CPUs / 2 GB, 64 fibers over 4 net.box connections, 10 s per op | memtx get 362k/s (p99 0.52 ms), memtx replace 231k/s, vinyl replace 94k/s (p99.9 70 ms), `transfer()` stored procedure 62.6k tx/s (p99 3.6 ms) |
| [go/lua-bench/tarantool](../go/lua-bench/tarantool) | 2026-10-04 | the go/rueidis-lua-bench workload over IPROTO (go-tarantool v3), `-n 1000000 -c 50 -keys 100000`, single node 2 CPUs / 2 GB | put 175k, get 293k, `add` 198k, `update` 111k, `delete` 187k ops/s; an earlier run on a busier VM: 61k–236k (Valkey 9.1.2 with the same flags, RESP: 407k–550k ops/s) |

## Known issues

- With `database.use_mvcc_engine: true`, a read-then-write procedure under the default
  `best-effort` isolation aborted ~8% of calls with `Transaction has been aborted by conflict`;
  `txn_isolation = 'read-committed'` fixed it (see [single-node](single-node/README.md#known-issues)).
- Keep hot transactions memtx-only: a vinyl statement can yield, and concurrent transactions then
  conflict.
- The image sets `TT_INSTANCE_NAME`; running a client script with the image needs
  `env -u TT_INSTANCE_NAME tarantool script.lua`.
