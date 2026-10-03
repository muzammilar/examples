# Dragonfly: built-in features

One [Dragonfly](https://www.dragonflydb.io/) v2.0.0 node (2 threads, 1 GB maxmemory) exercising what it ships with that a stock Valkey server doesn't: memcached protocol, JSON, search with vectors, Bloom filters, emulated cluster mode, and its own snapshot format.

## Quick start

```bash
make up         # start, wait for the healthcheck, print version, mode and thread count
make memcached  # set/get/incr over the memcached text protocol, read back through the Redis API
make json       # JSON.SET, JSON.GET with JSONPath, JSON.NUMINCRBY, JSON.ARRAPPEND
make search     # FT.CREATE on JSON, FT.SEARCH with text/numeric/tag filters, FT.AGGREGATE, KNN
make bloom      # BF.RESERVE, BF.MADD, BF.MEXISTS
make cluster    # CLUSTER INFO/SHARDS in emulated mode, SET/GET through valkey-cli -c
make snapshot   # SAVE + BGSAVE to /data, restart the container, the data is back
make test       # all of the above
make cli        # interactive valkey-cli
make down       # remove the container and the volume
```

| Port | Protocol |
|---|---|
| `127.0.0.1:6392` | Redis API |
| `127.0.0.1:11212` | memcached |

The scripts in [`scripts/`](scripts) run in a `tools` container (`valkey/valkey` alpine: `valkey-cli` and busybox `nc`), exit non-zero when a result is wrong, and can be rerun.

## What each target checks

| Target | Flag / setup | Result |
|---|---|---|
| `memcached` | `--memcached_port=11211`: second listener, same keyspace | `set`, `get`, `incr` via `nc` return `STORED`, `VALUE ... END` and the new counter; `GET visits` over the Redis API returns the counter; a key set with `SET` is readable with memcached `get` |
| `json` | | `JSON.GET user:1 '$.address.city'` returns `["Oslo"]`; `JSON.NUMINCRBY` bumps `age` to 32; `JSON.ARRAPPEND` adds a tag; final `JSON.GET` prints the document |
| `bloom` | | `BF.RESERVE seen:emails 0.001 1000`, add two addresses; `BF.MEXISTS` answers `1 1 0` for the two known and one unknown |
| `cluster` | `--cluster_mode=emulated` | Node answers `CLUSTER` commands as a one-shard cluster: `CLUSTER SHARDS` lists one master with slots 0-16383; `valkey-cli -c` works without redirects. For applications that only have a cluster-mode client. |

### search

Index over `product:*` JSON documents: `name` (TEXT), `price` (NUMERIC, sortable), `category` (TAG), `embedding` (4-dim FLOAT32 VECTOR, FLAT, L2). Four products.

| Query | Result |
|---|---|
| `@name:running @price:[0 50]` | only the shirt |
| `@category:{shoes}` sorted by price desc | shoes, highest first |
| `FT.AGGREGATE ... GROUPBY 1 @category REDUCE COUNT ... REDUCE AVG 1 @price` | 2 shoes, average price 100 |
| `*=>[KNN 2 @embedding $v AS dist]` with `[1,0,0,0]` | red shoe (distance 0), then trail shoe |
| same KNN with `@price:[0 100]` pre-filter | trail shoe (120) drops out |

The query vector is passed as raw float32 bytes with `valkey-cli -x`, which reads the last argument from stdin.

### snapshot

`--dir /data --dbfilename dump` writes Dragonfly's `.dfs` format: `dump-summary.dfs` plus one `dump-NNNN.dfs` per thread, written in parallel. The script runs `SAVE` and `BGSAVE`, prints `INFO persistence` (`last_saved_file`, `saving`) and lists `/data`; after `docker compose restart` the keys, including a JSON document, are loaded back. Dragonfly also saves on a clean shutdown when `dbfilename` is set.

## Known issues

- **`rdb_bgsave_in_progress` stays at 1 after a `BGSAVE`** in v2.0.0. Use `saving` to see whether a save is running.
