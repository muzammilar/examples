# Dragonfly: native cluster mode (3 shards x master + replica)

Six Dragonfly v2.0.0 nodes with `--cluster_mode=yes`: three shards, each a master and one replica. Open-source Dragonfly has no control plane and no gossip, so [`cluster.sh`](cluster.sh) is the control plane: it reads node ids, builds the topology as JSON, pushes it to every node with `DFLYCLUSTER CONFIG`, then attaches the replicas with `REPLICAOF`.

## Quick start

```bash
make up        # start, wait for healthchecks, push the config, wait for cluster_state:ok everywhere
make test      # CLUSTER INFO/SLOTS/SHARDS, 1000 keys via valkey-cli -c, MOVED, replicas in sync
make migrate   # move 1000 slots from one shard to another, then push the final config
make failover  # stop the master of slot 0, promote its replica by hand, bring the old node back as replica
make status    # cluster info + one line per node: role, keys, slots, node id
make cli       # valkey-cli -c inside the compose network
make bench     # bench-resp, then bench-lua, against the cluster
make emulated  # one node with --cluster_mode=emulated, CLUSTER SHARDS against it
make down      # remove containers and volumes
```

| | |
|---|---|
| Nodes | `dragonfly-1..6` on `127.0.0.1:7201` to `:7206` |
| Per node | `--proactor_threads=1 --maxmemory=512mb`, limited to 1 CPU and 512 MiB |
| Client | `tools` service (valkey/valkey); the config announces hostnames, so MOVED replies only resolve inside the compose network |
| Admin port | not needed: `DFLYCLUSTER CONFIG` and slot migrations work over the normal port 6379 |

## Cluster config

A JSON array, one entry per shard:

```json
[{"slot_ranges": [{"start": 0, "end": 5460}],
  "master": {"id": "<CLUSTER MYID>", "ip": "dragonfly-1", "port": 6379},
  "replicas": [{"id": "...", "ip": "dragonfly-4", "port": 6379}]}, ...]
```

- Every node must get the same config. A node without one answers `ERR Cluster is not yet configured`.
- Node ids are random per process start (`CLUSTER MYID`) unless `--cluster_node_id` is set. `cluster.sh` rebuilds the running config from `CLUSTER NODES` before each change.

## Migrate

1. The source shard gets a `migrations` entry: `{"slot_ranges": [{"start": 0, "end": 999}], "node_id": "<target id>", "ip": "dragonfly-2", "port": 6379}`.
2. After that config is pushed, the source streams the keys of those slots to the target; `DFLYCLUSTER SLOT-MIGRATION-STATUS` on both sides goes `CONNECTING`, `SYNC`, `FINISHED`.
3. The slots belong to the source until a config without `migrations` lists them under the target.

`make migrate` waits for `FINISHED` on both nodes (60 s), pushes that config, checks the owner of the range and reads all 1000 keys back. The target's replica gets the keys too.

## Failover

Nothing promotes a replica on its own. `make failover`:

1. Stops the master of slot 0, runs `REPLICAOF NO ONE` on its replica, and pushes a config with the replica as master and no replicas for that shard.
2. Reads the 1000 keys back and writes 100 more.
3. Starts the old node again (empty config, new id), adds it as a replica in the config, attaches it with `REPLICAOF`; it has the 100 new keys once in sync.

## Emulated mode

`--cluster_mode=emulated` runs a single node that answers `CLUSTER SHARDS/SLOTS/INFO` as if it owned all 16384 slots, so cluster clients can talk to one plain Dragonfly.

## Benchmark

Same two benchmarks and settings as the other Valkey and Dragonfly examples: `BENCH_N=200000` operations, `BENCH_C=50` clients, `BENCH_KEYS=100000` keys. Both run in the compose network against `dragonfly-1`.

| Target | What |
|---|---|
| `make bench-resp` | `valkey-benchmark --cluster -t set,get,incr,lpush,hset` from the `tools` container, at `-P 1` and `-P 16`. Reads the three masters and their slots from `CLUSTER NODES` and sends each key to its owner. |
| `make bench-lua` | [`go/rueidis-lua-bench`](../../go/rueidis-lua-bench) (linked as `bench`): SET, GET and three Lua scripts through rueidis, which reads the slot map and sends each command to the owning master. Every script touches one key, so Dragonfly's rule that scripts only touch their `KEYS` holds. |

Same per-node limits as the Valkey cluster in [`valkey/helm-chart`](../../valkey/helm-chart) (1 CPU, 512 MiB, one thread for commands). This cluster trails it on every test but GET in the Lua bench: 1.5-4x on most, 5.5x on pipelined SET, RESP p99 5-18 ms vs under 1 ms. `update.lua` is the slowest script at 125k vs 316k ops/s.

`BENCH_N=1000000 make bench`, Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03, one setup at a time.

`valkey-benchmark` (RESP), 50 clients, 100k keys:

| op | ops/s | p50 ms | p99 ms | ops/s `-P 16` | p50 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| SET | 173,461 | 0.07 | 4.86 | 360,881 | 0.38 | 17.84 |
| GET | 265,745 | 0.05 | 5.99 | 994,036 | 0.21 | 6.33 |
| INCR | 153,327 | 0.07 | 6.20 | 441,696 | 0.36 | 18.30 |
| LPUSH | 113,947 | 0.08 | 5.88 | 496,524 | 0.31 | 14.34 |
| HSET | 147,449 | 0.09 | 7.45 | 440,917 | 0.36 | 15.41 |

`bench-lua` (Go, rueidis, cluster, 6 nodes), 50 workers, 100k keys:

| op | ops/s | p50 ms | p99 ms |
|---|---:|---:|---:|
| SET | 256,623 | 0.18 | 0.50 |
| GET | 437,457 | 0.11 | 0.26 |
| add.lua | 283,854 | 0.16 | 0.54 |
| update.lua | 125,438 | 0.37 | 1.02 |
| delete.lua | 296,179 | 0.15 | 0.53 |

## Known issues

- **`WARNING: Could not fetch node CONFIG`** from `valkey-benchmark --cluster`, once per master. Harmless.
- **`valkey-benchmark --cluster` ops/s is quantized.** It notices a test is done only on a 250 ms timer, so ops/s is `BENCH_N` divided by a multiple of 0.25 s (800000, 400000, 266667, ...). Latencies are not affected.
