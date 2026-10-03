# Dragonfly — native cluster mode (3 shards x master + replica)

Six Dragonfly v2.0.0 nodes started with `--cluster_mode=yes`: three shards, each a master and one
replica. Open source Dragonfly has no control plane and no gossip, so you are the control plane:
[`cluster.sh`](cluster.sh) reads node ids, builds the topology as JSON and pushes it to every node
with `DFLYCLUSTER CONFIG`, then attaches the replicas with `REPLICAOF`.

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

- Nodes: `dragonfly-1..6`, `127.0.0.1:7201` to `:7206`. The config announces the hostnames, so
  MOVED replies only resolve inside the compose network; the `tools` service (valkey/valkey) is the
  cluster client.
- `--proactor_threads=1 --maxmemory=512mb` per node, each limited to 1 CPU and 512 MiB. `DFLYCLUSTER CONFIG` and slot migrations work
  over the normal port 6379, no `--admin_port` needed.

The config is a JSON array with one entry per shard:

```json
[{"slot_ranges": [{"start": 0, "end": 5460}],
  "master": {"id": "<CLUSTER MYID>", "ip": "dragonfly-1", "port": 6379},
  "replicas": [{"id": "...", "ip": "dragonfly-4", "port": 6379}]}, ...]
```

Every node has to get the same config. A node without one answers `ERR Cluster is not yet configured`.
Node ids are random per process start (`CLUSTER MYID`) unless `--cluster_node_id` is set.
`cluster.sh` rebuilds the running config from `CLUSTER NODES` before each change.

## Benchmark

`make bench` runs the same two benchmarks as the other Valkey and Dragonfly examples, with the same
settings: `BENCH_N=200000` operations, `BENCH_C=50` clients, `BENCH_KEYS=100000` keys. Both run in
the compose network against `dragonfly-1`.

`make bench-resp` is `valkey-benchmark --cluster -t set,get,incr,lpush,hset` from the `tools`
container, at `-P 1` and `-P 16`. `--cluster` works here: it reads the three masters and their
slots from `CLUSTER NODES` and sends each key to its owner. It prints `WARNING: Could not fetch
node CONFIG` for each master, which is harmless. In cluster mode it only notices that a test is
done on a 250 ms timer, so ops/s is `BENCH_N` divided by a multiple of 0.25 s (800000, 400000,
266667, ...); latencies are not affected.

`make bench-lua` runs [`go/rueidis-lua-bench`](../../go/rueidis-lua-bench), linked as `bench`:
SET, GET and three Lua scripts through rueidis.
rueidis reads the slot map from the node and sends each command to the master that owns the key.
Every script touches one key, so Dragonfly's rule that scripts only touch their `KEYS` holds.

Every node has the same limits as the Valkey cluster in
[`valkey/helm-chart`](../../valkey/helm-chart) (1 CPU, 512 MiB, one thread for commands).

At 1 CPU and 1 thread per node this cluster trails the Valkey one in [`valkey/helm-chart`](../../valkey/helm-chart) on every test but GET in the Lua bench: 1.5-4x on most, 5.5x on pipelined SET, with RESP p99 at 5-18 ms against under 1 ms. `update.lua` is the slowest script at 125k vs 316k ops/s.

Results from `BENCH_N=1000000 make bench` on an Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03, one setup at a time:

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

## Migrate

The source shard gets a `migrations` entry:
`{"slot_ranges": [{"start": 0, "end": 999}], "node_id": "<target id>", "ip": "dragonfly-2", "port": 6379}`.
After that config is pushed, the source streams the keys of those slots to the target, and
`DFLYCLUSTER SLOT-MIGRATION-STATUS` on both sides goes `CONNECTING`, `SYNC`, `FINISHED`. The slots
still belong to the source until you push a config without `migrations` that lists them under the
target. `make migrate` waits for `FINISHED` on both nodes (60 s), pushes that config, checks the
owner of the range and reads all 1000 keys back. The target's replica gets the keys too.

## Failover

Nothing promotes a replica on its own. `make failover` stops the master of slot 0, runs
`REPLICAOF NO ONE` on its replica and pushes a config with the replica as master and no replicas
for that shard. The 1000 keys are read back and 100 more written. The old node is then started
again with an empty config and a new id, added as a replica in the config, and attached with
`REPLICAOF`; it has the 100 new keys once it is in sync.

## Emulated mode

`--cluster_mode=emulated` runs a single node that answers `CLUSTER SHARDS/SLOTS/INFO` as if it
owned all 16384 slots, so cluster clients can talk to one plain Dragonfly.
