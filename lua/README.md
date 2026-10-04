# lua

Lua scripts used in this repo, in one place. Each entry is a symlink to the `lua/` folder of the
program that runs it, which keeps the real files (`go:embed` and Docker build contexts do not
follow symlinks).

## versioned-records

Create, update and delete one record with a version number, each as a single server-side call.
The update is a compare-and-set: it only applies if the version the client expects is still the
stored one (optimistic locking), so no read is needed first.

The benchmark records are inventory items: `{name: "item", qty: <n>, version: <v>}`.

| Script | Does | Returns |
|---|---|---|
| `add.lua` | create the record with `version` 1, only if the key does not exist | 1 created, 0 already exists |
| `update.lua` | set fields and increment `version`, only if `version` equals the expected value (`''` = any) | new version, 0 missing, -1 version mismatch |
| `delete.lua` | delete records, optionally only those at an expected version | number deleted |

| Folder | Real path | Systems | Run by |
|---|---|---|---|
| [`versioned-records/rueidis-lua-bench`](versioned-records/rueidis-lua-bench) | [`go/rueidis-lua-bench/lua`](../go/rueidis-lua-bench/lua) | Redis protocol: Valkey, Dragonfly, Redis (`redis.call`, `EVALSHA`) | [`go/rueidis-lua-bench`](../go/rueidis-lua-bench) |
| [`versioned-records/tarantool`](versioned-records/tarantool) | [`go/lua-bench/tarantool/lua`](../go/lua-bench/tarantool/lua) | Tarantool (stored functions on `box.space`, called over IPROTO) | [`go/lua-bench/tarantool`](../go/lua-bench/tarantool) |
| [`versioned-records/aerospike`](versioned-records/aerospike) | [`go/lua-bench/aerospike/lua`](../go/lua-bench/aerospike/lua) | Aerospike (one record-UDF module `bench.lua` with add/update/delete) | [`go/lua-bench/aerospike`](../go/lua-bench/aerospike) |

The `tarantool` and `aerospike` links resolve once the `go-lua-bench-*` branches are on `main`.
