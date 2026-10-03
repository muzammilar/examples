# Dragonfly: built-in features

One [Dragonfly](https://www.dragonflydb.io/) v2.0.0 node (2 threads, 1 GB maxmemory) showing things
it ships with that a stock Valkey server doesn't: the memcached protocol, JSON, search with vectors,
Bloom filters, an emulated cluster mode and its own snapshot format.

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

- Redis API: `127.0.0.1:6392`, memcached: `127.0.0.1:11212`
- The scripts in [`scripts/`](scripts) run in a `tools` container (`valkey/valkey` alpine: `valkey-cli`
  and busybox `nc`) and exit non-zero when a result is wrong. They can run again.

## memcached

`--memcached_port=11211` opens a second listener for the memcached text protocol on the same keyspace.
The script sends `set`, `get` and `incr` with `nc` and gets `STORED`, `VALUE ... END` and the new
counter values; `GET visits` over the Redis API then returns the counter, and a key set with
`SET` is readable with the memcached `get`.

## json

`JSON.GET user:1 '$.address.city'` returns `["Oslo"]`, `JSON.NUMINCRBY` bumps `age` to 32 and
`JSON.ARRAPPEND` adds a tag; the final `JSON.GET` prints the whole document.

## search

An index over `product:*` JSON documents with `name` (TEXT), `price` (NUMERIC, sortable),
`category` (TAG) and `embedding` (4-dim FLOAT32 VECTOR, FLAT, L2). Four products, then:

- `@name:running @price:[0 50]` finds only the shirt
- `@category:{shoes}` sorted by price, highest first
- `FT.AGGREGATE ... GROUPBY 1 @category REDUCE COUNT ... REDUCE AVG 1 @price`: 2 shoes, average price 100
- `*=>[KNN 2 @embedding $v AS dist]` with `[1,0,0,0]`: the red shoe (distance 0), then the trail shoe
- the same KNN with `@price:[0 100]` as a pre-filter: the trail shoe (120) drops out

The query vector is passed as raw float32 bytes with `valkey-cli -x`, which reads the last
argument from stdin.

## bloom

`BF.RESERVE seen:emails 0.001 1000`, add two addresses, `BF.MEXISTS` answers `1 1 0` for the two
known ones and an unknown one.

## cluster

`--cluster_mode=emulated` makes the single node answer `CLUSTER` commands as a one-shard cluster:
`CLUSTER SHARDS` lists one master with slots 0-16383, and a cluster client (`valkey-cli -c`) works
without redirects. Useful when the application only has a cluster-mode client.

## snapshot

`--dir /data --dbfilename dump` writes Dragonfly's `.dfs` format: `dump-summary.dfs` plus one
`dump-NNNN.dfs` per thread, written in parallel. The script runs `SAVE` and `BGSAVE`, prints
`INFO persistence` (`last_saved_file`, `saving`) and lists `/data`; after `docker compose restart`
the keys, including a JSON document, are loaded back. Dragonfly also saves on a clean shutdown when
`dbfilename` is set. In v2.0.0 `rdb_bgsave_in_progress` stays at 1 after a `BGSAVE`; `saving`
shows whether a save is running.
