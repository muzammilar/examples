# rueidis-lua-bench

Go benchmark for Redis-compatible servers using [rueidis](https://github.com/redis/rueidis).
Prints ops/s, p50 and p99 per command. Works on a single node, a primary or a cluster: rueidis
detects cluster mode on connect and routes each key to its primary.

## What it measures

| Command | What |
|---------|------|
| `SET`, `GET` | plain string ops |
| [`lua/add.lua`](lua/add.lua) | add a hash |
| [`lua/update.lua`](lua/update.lua) | update a hash with a version check |
| [`lua/delete.lua`](lua/delete.lua) | delete a hash |

## Quick start

```sh
go run . -addr 127.0.0.1:6379 -n 200000 -c 50 -keys 100000
```

| Flag | Default | Meaning |
|------|---------|---------|
| `-addr` | `127.0.0.1:6379` | server; any node of a cluster |
| `-n` | 200000 | operations per command |
| `-c` | 50 | concurrent workers |
| `-keys` | 100000 | distinct keys |
| `-wait` | 0 | if > 0, send `WAIT <n> 0` with every write (same round trip, on a dedicated connection per worker) and fail if fewer replicas acknowledged; standalone primary only |

The [`Dockerfile`](Dockerfile) builds a static image.

Used by the Valkey and Dragonfly examples as a `bench` symlink (`bench -> ../../go/rueidis-lua-bench`),
run with `make bench-lua`: [valkey/minimal-lua](../../valkey/minimal-lua),
[valkey/helm-chart](../../valkey/helm-chart), [valkey/valkey-operator](../../valkey/valkey-operator),
[valkey/ot-redis-operator](../../valkey/ot-redis-operator),
[dragonfly/single-node](../../dragonfly/single-node),
[dragonfly/docker-compose-cluster](../../dragonfly/docker-compose-cluster),
[dragonfly/helm-chart](../../dragonfly/helm-chart),
[dragonfly/dragonfly-operator](../../dragonfly/dragonfly-operator).

## Results

Valkey 9.1.2 vs Dragonfly v2.0.0, `-n 1000000 -c 50 -keys 100000`, Apple M4 Pro (Docker Desktop
VM), one setup at a time, 2026-10-03. Valkey was ahead in every setup, most on the Lua scripts.

| Setup | Lua scripts | SET/GET |
|-------|-------------|---------|
| Single node, 2 CPUs each | Valkey 2.7-3x faster (`add.lua` 490k vs 170k ops/s) | Valkey 1.6x faster (539k vs 337k) |
| Cluster, 3 primaries + 3 replicas, 1 CPU per node | Valkey 1.5-2.5x faster (`update.lua` 316k vs 125k ops/s) | GET about even |
| kind, primary + replicas | `add.lua` on the primary: Valkey 364k, Dragonfly Helm chart 149k, Dragonfly operator 253k ops/s | — |

Valkey vs Tarantool 3.8.1 (the [`go/lua-bench/tarantool`](../lua-bench/tarantool) port of this
workload) at matching durability (no AOF / `wal.mode none`, `appendfsync no` / `everysec` vs
`write`, `always` vs `fsync`), on a single node, 1 primary + 2 replicas (async, and `-wait 1` vs
synchronous spaces) and 3 shards: [`tarantool/lua-bench-vs-valkey`](../../tarantool/lua-bench-vs-valkey#results).

Notes:

- kind pod resources differ between setups, so treat that row as rough.
- Full tables, with `valkey-benchmark` RESP numbers alongside, are in each example's README.
