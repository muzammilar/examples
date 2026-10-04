# lua-bench/tarantool

The workload of [`go/rueidis-lua-bench`](../../rueidis-lua-bench) on Tarantool, with the same
flags and output (ops/s, p50, p99 per command), so the numbers line up with the Valkey and
Dragonfly runs. Tarantool does not speak RESP: requests go over IPROTO (MessagePack) through the
official [go-tarantool](https://github.com/tarantool/go-tarantool) connector (v3.0.2, the current
major). Same workload, different wire protocol.

## What it measures

| op | rueidis-lua-bench equivalent | Tarantool request |
|---|---|---|
| `put` | `SET` | `REPLACE` into memtx space `bench_s` (no Lua) |
| `get` | `GET` | `SELECT` by primary key (no Lua) |
| `add` | [`add.lua`](../../rueidis-lua-bench/lua/add.lua) | `CALL bench_add(key, fields)` ([`lua/add.lua`](lua/add.lua)): insert at version 1 if absent; 1 created, 0 exists |
| `update` | [`update.lua`](../../rueidis-lua-bench/lua/update.lua) | `CALL bench_update(key, expected, fields)` ([`lua/update.lua`](lua/update.lua)): set fields, bump version if it is `expected` (`''` = any); new version, 0 missing, -1 mismatch |
| `delete` | [`delete.lua`](../../rueidis-lua-bench/lua/delete.lua) | `CALL bench_delete(keys, expected)` ([`lua/delete.lua`](lua/delete.lua)): number deleted (`''` = any version) |

- Records are inventory items: `bench_h {key, version, fields}` with `fields = {name = 'item',
  qty = '<n>'}`. On start the program creates and empties `bench_s {key, value}` and `bench_h`
  with one `EVAL`, then `EVAL`s each `lua/*.lua`, which defines its function as a Lua global
  (in the instance's memory until it restarts). The user needs eval and space-creation rights
  (role `super`).
- Key layout, CAS logic and worker split are the same as rueidis-lua-bench: worker `w` owns its
  slice of keys, so `update` passes the exact expected version and fails the run on `-1`.
- go-tarantool multiplexes concurrent requests over one connection, like rueidis's
  auto-pipelining.

## Quick start

```sh
go run . -addr 127.0.0.1:3301 -user app -password secret -n 200000 -c 50 -keys 100000
# or in Docker, on the server's network:
docker build -t lua-bench-tarantool:local .
docker run --rm --network tarantool-single_default lua-bench-tarantool:local \
  -addr tarantool:3301 -user app -password secret -n 1000000 -c 50 -keys 100000
```

| flag | default | meaning |
|---|---|---|
| `-addr` | `127.0.0.1:3301` | Tarantool; for a replicaset, the leader (the others are read-only) |
| `-n` | 200000 | operations per command |
| `-c` | 50 | concurrent workers |
| `-keys` | 100000 | distinct keys |
| `-user` / `-password` | `guest` / empty | credentials |
| `-router` | false | `-addr` is a vshard router that defines `bench_reset`, `bench_put`, `bench_get`, `bench_add`, `bench_update`, `bench_delete` (see [`tarantool/lua-bench-vs-valkey/tarantool/vshard`](../../../tarantool/lua-bench-vs-valkey/tarantool/vshard)); no schema or function setup from the client |

## Results

`-n 1000000 -c 50 -keys 100000` (as in the Valkey/Dragonfly runs), Apple M4 Pro, Docker VM
aarch64, Tarantool 3.8.1 capped at 2 CPUs / 2 GB per instance (`docker update`), client in a
container on the same Docker network without a CPU cap, 2026-10-04, one run each, Docker VM
shared with other agents:

| op | single node ([`tarantool/single-node`](../../../tarantool/single-node)) ops/s | p50 ms | p99 ms |
|---|---:|---:|---:|
| `put` | 175,238 | 0.24 | 1.22 |
| `get` | 292,625 | 0.16 | 0.28 |
| `add` | 198,419 | 0.22 | 0.66 |
| `update` | 111,169 | 0.40 | 1.13 |
| `delete` | 187,135 | 0.21 | 1.15 |

An earlier run of the same workload (functions in one setup file, `nil` instead of `''` for "any
version") an hour before, while other agents' containers were busy, gave 133,633 / 236,126 /
132,667 / 60,541 / 137,410 ops/s on the single node and 125,569 / 259,282 / 142,015 / 70,033 /
188,231 on the leader of the 3-instance replicaset
([`tarantool/docker-compose-cluster`](../../../tarantool/docker-compose-cluster), asynchronous
spaces). Treat the spread between runs as noise of the shared VM.

For comparison, rueidis-lua-bench on Valkey 9.1.2 (2 CPUs, `--io-threads 2`) with the same flags
on 2026-10-03: SET 538,655, GET 550,445, `add.lua` 489,724, `update.lua` 406,756, `delete.lua`
508,709 ops/s; Dragonfly about 170k for `add.lua` ([`go/rueidis-lua-bench`](../../rueidis-lua-bench#results)).

- Different protocol (IPROTO/MessagePack vs RESP) and different durability: Tarantool wrote
  every commit to its WAL (`wal.mode: write`); the Valkey run had no AOF and Valkey's default
  RDB snapshots (`save 3600 1 300 100 60 10000`, the image has no config file).
- At matching durability, one setup at a time:
  [`tarantool/lua-bench-vs-valkey`](../../../tarantool/lua-bench-vs-valkey#results). Without
  fsync Valkey was 1.8x–2.7x Tarantool on every op (put 214,707 vs SET 520,961 ops/s at
  `write` / `appendfsync no`); with fsync per commit Tarantool was ahead on put (67,365 vs
  49,517 ops/s, 1.36x) and update (79,728 vs 45,890 ops/s, 1.74x).
- `update` is the slowest op: it copies the tuple's field map in Lua, merges it and replaces
  the whole tuple.
