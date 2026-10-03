# Valkey: Lua scripts from Go

One Valkey 9.1.2 container and a Go program ([`main.go`](main.go)) that uses
[rueidis](https://github.com/redis/rueidis) to add, update and delete hashes through the Lua
scripts in [`lua/`](lua). Each hash has a `version` field that the scripts bump.

```bash
make up           # start valkey on 127.0.0.1:6390
make test         # build the Go program and run it in compose
make run-local    # go run . against $VALKEY_ADDR (127.0.0.1:6390)
make scripts      # SCRIPT LOAD each lua/*.lua and print its SHA1
make bench        # bench-resp, then bench-lua
make bench-resp   # valkey-benchmark from a container in the compose network
make bench-lua    # go/rueidis-lua-bench (linked as bench) in compose
make bench-local  # bench-lua with go run against $VALKEY_ADDR
make cli          # valkey-cli
make down         # remove the container, volume and built images
```

| Script | KEYS | ARGV | Returns |
|---|---|---|---|
| [`add.lua`](lua/add.lua) | key | `field value ...` | `1` created at version 1, `0` already exists |
| [`update.lua`](lua/update.lua) | key | expected version (`''` for any), `field value ...` | new version, `0` missing, `-1` version mismatch |
| [`delete.lua`](lua/delete.lua) | keys | expected version (`''` for any) | number deleted |

The scripts are embedded with `//go:embed` and wrapped in `rueidis.NewLuaScript`. `Exec` sends
`EVALSHA`, and if the server replies `NOSCRIPT` (after a restart or `SCRIPT FLUSH`) it resends
the script with `EVAL`, which caches it again. So nothing has to be loaded up front; `SCRIPT LOAD`
(as in `make scripts`) just fills the cache ahead of time. The program flushes the cache first to
show the fallback, then runs a few updates at a stale version and a small CAS race.

## Benchmark

`make bench` runs two benchmarks with the same settings: `BENCH_N=200000` operations,
`BENCH_C=50` clients, `BENCH_KEYS=100000` keys.

`make bench-resp` runs `valkey-benchmark -t set,get,incr,lpush,hset` from a
`valkey/valkey:9.1.2-alpine` container in the compose network, once without pipelining (`-P 1`)
and once with 16 commands per round trip (`-P 16`).

`make bench-lua` runs [`go/rueidis-lua-bench`](../../go/rueidis-lua-bench), linked as `bench`:
SET, GET and the same three scripts, from 50 goroutines. Each goroutine owns its slice of the keys,
so `update.lua` is a real CAS at the version it expects. rueidis auto-pipelines the concurrent
calls over one connection.

The server is limited to 2 CPUs and 1 GiB and runs with `--io-threads 2`, the same as
[`dragonfly/single-node`](../../dragonfly/single-node) with `--proactor_threads=2`, which runs the
same two benchmarks. `make bench-local` goes through Docker's port forwarding and is much slower.

Next to [`dragonfly/single-node`](../../dragonfly/single-node) on the same 2 CPUs, Valkey is about 15% faster on plain RESP and 2.7-3x faster on the Lua scripts.

Results from `BENCH_N=1000000 make bench` on an Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03, one setup at a time:

`valkey-benchmark` (RESP), 50 clients, 100k keys:

| op | ops/s | p50 ms | p99 ms | ops/s `-P 16` | p50 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| SET | 268,240 | 0.10 | 0.18 | 3,401,360 | 0.14 | 0.38 |
| GET | 269,978 | 0.10 | 0.17 | 3,546,099 | 0.13 | 0.26 |
| INCR | 275,103 | 0.10 | 0.17 | 3,448,276 | 0.14 | 0.29 |
| LPUSH | 265,041 | 0.10 | 0.18 | 3,717,472 | 0.17 | 0.26 |
| HSET | 269,687 | 0.10 | 0.19 | 3,048,780 | 0.22 | 0.39 |

`bench-lua` (Go, rueidis), 50 workers, 100k keys:

| op | ops/s | p50 ms | p99 ms |
|---|---:|---:|---:|
| SET | 538,655 | 0.09 | 0.20 |
| GET | 550,445 | 0.09 | 0.19 |
| add.lua | 489,724 | 0.10 | 0.21 |
| update.lua | 406,756 | 0.12 | 0.23 |
| delete.lua | 508,709 | 0.09 | 0.20 |

Tools: run `direnv allow` (or `nix develop`) at the repo root. Without Nix you need Docker, and
Go for `make run-local` and `make bench-local`.
