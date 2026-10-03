# Valkey: 3-leader cluster on kind with the OT redis-operator

[OT-CONTAINER-KIT redis-operator](https://github.com/OT-CONTAINER-KIT/redis-operator) managing a `RedisCluster` ([`rediscluster.yaml`](rediscluster.yaml)) that runs `valkey/valkey:9.1.2-alpine`: 3 leaders, 3 followers, data and `nodes.conf` on PVCs.

| Component | Version |
|---|---|
| Helm chart `ot-helm/redis-operator` | `0.26.1` |
| Operator | `v0.26.0` |
| Valkey image | `valkey/valkey:9.1.2-alpine` |

## Quick start

```bash
make up              # kind cluster, operator, RedisCluster; waits for Ready
make test            # cluster state, masters/replicas, 30 keys via valkey-cli -c
make scale-up        # clusterSize +1 (or PRIMARIES=N), then make test
make scale-down      # clusterSize -1 (or PRIMARIES=N, at least 3), then make test
make failover        # delete a master pod, check its replica takes over
make failover HARD=1 # SIGSTOP the master first, so no graceful handover
make status          # RedisCluster, pods, CLUSTER NODES
make bench           # bench-resp, then bench-lua, from pods against the cluster
make cli             # valkey-cli -c on valkey-leader-0
make down            # delete the kind cluster
```

`make kind-up` only creates the kind cluster. Pod names (`valkey-leader-N`, `valkey-follower-N`) are the initial roles; after a failover a follower pod can be a master.

Tools: `kind`, `kubectl`, `helm`, from `direnv allow` or `nix develop` at the repo root. On Linux:

```bash
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
curl -fsSLo kind https://kind.sigs.k8s.io/dl/v0.32.0/kind-linux-$ARCH && sudo install kind /usr/local/bin/
curl -fsSLO https://dl.k8s.io/release/v1.37.1/bin/linux/$ARCH/kubectl && sudo install kubectl /usr/local/bin/
curl -fsSL https://get.helm.sh/helm-v4.3.0-linux-$ARCH.tar.gz | tar xz && sudo install linux-$ARCH/helm /usr/local/bin/
```

## What the scripts do

| Script | Does |
|---|---|
| [`test.sh`](test.sh) | Runs inside `valkey-leader-0`. Fails unless `cluster_state:ok`, all 16384 slots ok, and `clusterSize` masters with as many replicas. Writes and reads back 30 keys with `valkey-cli -c`, prints keys and slots per master. |
| [`scale.sh`](scale.sh) | Writes 100 keys, patches `spec.clusterSize`, waits until the cluster settles, reads the keys back. Scale-down deletes the PVCs of removed pods (see Known issues). |
| [`failover.sh`](failover.sh) | Writes 30 keys and deletes a master pod. The operator's preStop hook runs `CLUSTER FAILOVER` on its replica: promotion takes about 2 s and all keys read back. The recreated pod keeps its node ID and rejoins as a replica. With `HARD=1` the master is frozen instead, so the other nodes fail it over only after `cluster-node-timeout` (15 s), about 20 s in total; the frozen pod is then force-deleted. |

## Benchmark

Same two benchmarks and settings as the other Valkey and Dragonfly examples: `BENCH_N=200000` operations, `BENCH_C=50` clients, `BENCH_KEYS=100000` keys. Each runs in its own pod (`kubectl run --rm`) and starts from Service `valkey-leader`.

| Target | What |
|---|---|
| `make bench-resp` | `valkey-benchmark --cluster -t set,get,incr,lpush,hset` from a `valkey/valkey:9.1.2-alpine` pod, at `-P 1` and `-P 16` |
| `make bench-lua` | [`go/rueidis-lua-bench`](../../go/rueidis-lua-bench) (linked as `bench`): SET, GET and three Lua scripts through rueidis, which sees a cluster and sends each key to its leader. Image built locally and loaded with `kind load docker-image`. |

Resources as in [`rediscluster.yaml`](rediscluster.yaml): 500m CPU and 256Mi per pod. All six pods and the bench pod share one kind node.

Within about 25% of [`valkey-operator`](../valkey-operator) on the same kind node. There is no sharded Dragonfly on kind to compare with: the Dragonfly operator runs one primary with replicas.

`BENCH_N=1000000 make bench`, Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03, one setup at a time.

`valkey-benchmark` (RESP), 50 clients, 100k keys:

| op | ops/s | p50 ms | p99 ms | ops/s `-P 16` | p50 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| SET | 265,111 | 0.06 | 0.16 | 1,331,558 | 0.21 | 1.15 |
| GET | 398,248 | 0.04 | 0.10 | 1,984,127 | 0.10 | 0.32 |
| INCR | 306,560 | 0.06 | 0.14 | 1,329,787 | 0.21 | 1.33 |
| LPUSH | 283,930 | 0.06 | 0.15 | 994,036 | 0.23 | 17.50 |
| HSET | 248,756 | 0.07 | 0.15 | 663,130 | 0.47 | 30.99 |

`bench-lua` (Go, rueidis, cluster, 6 nodes), 50 workers, 100k keys:

| op | ops/s | p50 ms | p99 ms |
|---|---:|---:|---:|
| SET | 304,575 | 0.11 | 0.31 |
| GET | 432,392 | 0.10 | 0.26 |
| add.lua | 331,399 | 0.10 | 0.27 |
| update.lua | 166,841 | 0.13 | 0.40 |
| delete.lua | 341,887 | 0.10 | 0.26 |

## Known issues

- **No Valkey mode; relies on an alpha flag.** Valkey runs only because the operator is installed with `featureGates.GenerateConfigInInitContainer=true`: an init container writes `redis.conf` and the pod runs `redis-server` / `redis-cli`, which the Valkey image ships as symlinks. Without the flag the operator needs the entrypoint of its own Redis image.
- **Scale-up often stalls.** The operator rebalances right after `add-node`, the first `MIGRATE` fails with `CLUSTERDOWN`, and slots stay open while the operator just requeues. Workaround (in `scale.sh`): after 30 s of open slots, run `valkey-cli --cluster fix` and `--cluster rebalance --cluster-use-empty-masters`.
- **Removed pods' PVCs are kept.** A later scale-up would start those pods on the old `nodes.conf` and break. Workaround: scale-down deletes them.
- **`valkey-benchmark --cluster` ops/s is quantized.** It notices a test is done only on a 250 ms timer, so ops/s is `BENCH_N` divided by a multiple of 0.25 s (800000, 400000, 266667, ...). Latencies are not affected.
