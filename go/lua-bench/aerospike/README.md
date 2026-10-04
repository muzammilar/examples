# lua-bench/aerospike

Go benchmark for [Aerospike](https://aerospike.com/) using the official client
([aerospike-client-go v8](https://github.com/aerospike/aerospike-client-go)). It runs the
[rueidis-lua-bench](../../rueidis-lua-bench) workload twice: once as Lua record UDFs, once with
Aerospike's own operations. Prints ops/s, p50 and p99 per command.

The protocol is different from the Redis benchmark: the Aerospike wire protocol, with the client
sending each key straight to the node that masters its partition, against RESP. The Aerospike client
sends one request per call over a pool of connections; rueidis pipelines calls over one connection.

## What it measures

Records live in namespace `test`. As in the Redis benchmark they are inventory items: bins `name` ("item"), `qty` and `version`. The three UDFs are functions of one module, [`lua/bench.lua`](lua/bench.lua), instead of one script per file.

| Command | What | Redis equivalent |
|---------|------|------------------|
| `put`, `get` | `PutBins` / `Get` of one bin, set `kv` | `SET`, `GET` |
| `add udf` | [`lua/bench.lua`](lua/bench.lua) `add`: create at version 1 if absent | `add.lua` |
| `update udf` | `update`: set fields and bump the version if it is the expected one | `update.lua` |
| `delete udf` | `delete`: remove the record | `delete.lua` |
| `add native` | `PutBins` with `RecordExistsAction` `CREATE_ONLY` (exists: `KEY_EXISTS_ERROR`) | `add.lua` |
| `update native` | `Operate` with `GenerationPolicy` `EXPECT_GEN_EQUAL` and `UPDATE_ONLY`: put fields, add 1 to `version`, read it back (mismatch: `GENERATION_ERROR`) | `update.lua` |
| `delete native` | `Delete` | `delete.lua` |

The UDFs run through `client.Execute` on records in set `udf`; the native ops use set `native`. The record
generation starts at 1 and goes up by one per write, so it doubles as the version: the native
update checks the generation instead of reading the `version` bin. Each worker owns its own keys,
so it knows the expected version and every update must succeed; the run fails otherwise. As in the
Redis benchmark, each key is added and deleted several times, so most `add` calls find the record
already there and most `delete` calls find nothing.

The program registers `lua/bench.lua` as module `bench` on start and deletes its keys from an
earlier run.

## Quick start

```sh
go run . -addr 127.0.0.1:3000 -n 200000 -c 50 -keys 100000
```

| Flag | Default | Meaning |
|------|---------|---------|
| `-addr` | `127.0.0.1:3000` | server; any node of a cluster |
| `-n` | 200000 | operations per command |
| `-c` | 50 | concurrent workers |
| `-keys` | 100000 | distinct keys |

From the host, Docker Desktop's port forwarding costs a lot of throughput; run the image on the
example's Compose network instead (a cluster has to be reached that way, since nodes advertise
their container addresses):

```sh
docker build -t lua-bench-aerospike .
docker run --rm --network aerospike-single_default lua-bench-aerospike -addr aerospike:3000 -n 1000000 -c 50 -keys 100000
```

## Results

Aerospike CE 8.1.2.5, client v8.9.0, `-n 1000000 -c 50 -keys 100000`, Apple M4 Pro, Docker VM
aarch64, 2026-10-04, one run, bench container on the Compose network with no cap.

Single node ([`aerospike/single-node`](../../../aerospike/single-node), capped to 2 CPUs with
`docker update --cpus 2` like the Valkey and Dragonfly single nodes; the image's default namespace,
file-backed storage):

| op | ops/s | p50 ms | p99 ms |
|----|------:|-------:|-------:|
| put | 149,932 | 0.12 | 1.95 |
| get | 168,824 | 0.10 | 1.41 |
| add udf | 103,723 | 0.14 | 3.27 |
| update udf | 88,410 | 0.16 | 4.61 |
| delete udf | 112,004 | 0.15 | 4.30 |
| add native | 132,330 | 0.12 | 4.49 |
| update native | 89,979 | 0.16 | 6.96 |
| delete native | 95,833 | 0.15 | 6.05 |

- UDF vs native: the create-only put beat the `add` UDF by about 30%; the version-checked update
  was even (88k vs 90k ops/s) and the UDF delete came out ahead (most deletes find no record, which
  the UDF answers without a write). Within a single run's noise the Lua UDFs cost little here.
- Against the same workload on Redis-protocol servers in
  [rueidis-lua-bench](../../rueidis-lua-bench#results) (2 CPUs, same flags): Valkey 9.1.2 did
  `add.lua` at 490k and `SET` at 539k ops/s, Dragonfly v2.0.0 170k and 337k. Aerospike is
  3-5x behind Valkey on this shape. Not like for like: rueidis pipelines every call over one
  connection, the Aerospike client sends one request per connection at a time; Aerospike writes
  go to its storage engine (file here); and a Redis Lua script blocks the server while an Aerospike
  UDF runs inside a record lock on one of many service threads.
- Other agents' containers were running on the same Docker VM during the run.
