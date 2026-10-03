# Dragonfly: single node with Lua scripts from Go

One Dragonfly v2.0.0 container and the Go program from
[`valkey/minimal-lua`](../../valkey/minimal-lua), unchanged apart from the module path and the
address (`DRAGONFLY_ADDR`). It uses [rueidis](https://github.com/redis/rueidis) to add, update
and delete hashes through the Lua scripts in [`lua/`](lua).

```bash
make up           # start dragonfly on 127.0.0.1:6391
make test         # build the Go program and run it in compose
make run-local    # go run . against $DRAGONFLY_ADDR (127.0.0.1:6391)
make scripts      # SCRIPT LOAD each lua/*.lua and print its SHA1
make bench        # bench-resp, then bench-lua
make bench-resp   # valkey-benchmark from a container in the compose network
make bench-lua    # go/rueidis-lua-bench (linked as bench) in compose
make bench-local  # bench-lua with go run against $DRAGONFLY_ADDR
make cli          # redis-cli (shipped in the dragonfly image)
make down         # remove the container, volume and built images
```

The scripts are the same as in `valkey/minimal-lua`; see the table there. Dragonfly runs them
with Lua 5.4 (`unpack` is still there) and only lets a script touch the keys passed in `KEYS`,
which ours do. Touching any other key fails with `script tried accessing undeclared key` unless
you start the server with `--default_lua_flags=allow-undeclared-keys`. `SCRIPT FLUSH`,
`SCRIPT EXISTS`, `SCRIPT LOAD` and the `NOSCRIPT` reply behave as in Valkey, so the program's
flush-then-`EVAL`-fallback step works as is.

Dragonfly runs with `--proactor_threads=2` and `--maxmemory=1gb` to match the container limits.

## Benchmark

`make bench` runs the same two benchmarks as [`valkey/minimal-lua`](../../valkey/minimal-lua),
with the same settings: `BENCH_N=200000` operations, `BENCH_C=50` clients, `BENCH_KEYS=100000`
keys.

`make bench-resp` runs `valkey-benchmark -t set,get,incr,lpush,hset` from a
`valkey/valkey:9.1.2-alpine` container in the compose network, at `-P 1` and `-P 16`. It prints
`WARNING: Could not fetch server CONFIG` because Dragonfly doesn't answer its `CONFIG GET`; the
numbers are fine.

`make bench-lua` runs [`go/rueidis-lua-bench`](../../go/rueidis-lua-bench), linked as `bench`:
SET, GET and the same three scripts from 50 goroutines, with rueidis auto-pipelining the concurrent
calls over one connection.

Both servers are limited to 2 CPUs and 1 GiB, Dragonfly with `--proactor_threads=2` and Valkey
with `--io-threads 2`.

Dragonfly doesn't fly with Lua here: on the same 2 CPUs, [`valkey/minimal-lua`](../../valkey/minimal-lua) runs the three scripts 2.7-3x faster (`add.lua` 490k vs 170k ops/s), while plain RESP is within about 15%.

Results from `BENCH_N=1000000 make bench` on an Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03, one setup at a time:

`valkey-benchmark` (RESP), 50 clients, 100k keys:

| op | ops/s | p50 ms | p99 ms | ops/s `-P 16` | p50 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| SET | 231,160 | 0.12 | 0.22 | 2,380,952 | 0.30 | 0.53 |
| GET | 237,925 | 0.11 | 0.20 | 2,688,172 | 0.26 | 0.49 |
| INCR | 242,895 | 0.11 | 0.19 | 2,197,802 | 0.32 | 0.63 |
| LPUSH | 239,808 | 0.11 | 0.20 | 2,493,766 | 0.17 | 1.64 |
| HSET | 243,132 | 0.11 | 0.20 | 2,293,578 | 0.17 | 2.45 |

`bench-lua` (Go, rueidis), 50 workers, 100k keys:

| op | ops/s | p50 ms | p99 ms |
|---|---:|---:|---:|
| SET | 337,342 | 0.14 | 0.31 |
| GET | 340,967 | 0.14 | 0.30 |
| add.lua | 169,862 | 0.24 | 1.00 |
| update.lua | 150,092 | 0.27 | 1.14 |
| delete.lua | 171,920 | 0.24 | 0.99 |

Tools: run `direnv allow` (or `nix develop`) at the repo root. Without Nix you need Docker, and
Go for `make run-local` and `make bench-local`.
