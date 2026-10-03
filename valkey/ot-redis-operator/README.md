# Valkey: 3-leader cluster on kind with the OT redis-operator

[OT-CONTAINER-KIT redis-operator](https://github.com/OT-CONTAINER-KIT/redis-operator) (Helm chart
`ot-helm/redis-operator` `0.26.1`, operator `v0.26.0`) managing a `RedisCluster`
([`rediscluster.yaml`](rediscluster.yaml)) that runs `valkey/valkey:9.1.2-alpine`: 3 leaders,
3 followers, data and `nodes.conf` on PVCs.

The OT operator relies on an alpha flag: it has no Valkey mode. It runs Valkey only because it's
installed with `featureGates.GenerateConfigInInitContainer=true`. An init container writes
`redis.conf` and the pod runs `redis-server` / `redis-cli`, which the Valkey image ships as
symlinks. Without the flag the operator needs the entrypoint of its own Redis image.

Requires `kind`, `kubectl`, `helm`. `direnv allow` or `nix develop` at the repo root provides them.
On Linux:

```bash
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
curl -fsSLo kind https://kind.sigs.k8s.io/dl/v0.32.0/kind-linux-$ARCH && sudo install kind /usr/local/bin/
curl -fsSLO https://dl.k8s.io/release/v1.37.1/bin/linux/$ARCH/kubectl && sudo install kubectl /usr/local/bin/
curl -fsSL https://get.helm.sh/helm-v4.3.0-linux-$ARCH.tar.gz | tar xz && sudo install linux-$ARCH/helm /usr/local/bin/
```

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

`make kind-up` only creates the kind cluster. Pod names (`valkey-leader-N`, `valkey-follower-N`)
are the initial roles; after a failover a follower pod can be a master.

## Test

[`test.sh`](test.sh) runs inside `valkey-leader-0`. It fails unless `cluster_state:ok`, all 16384
slots are ok, and there are `clusterSize` masters and as many replicas. It then writes and reads
back 30 keys with `valkey-cli -c` and prints keys and slots per master.

## Scaling

[`scale.sh`](scale.sh) writes 100 keys, patches `spec.clusterSize`, waits until the cluster
settles, and reads the keys back. Scale-down needs no help.

Scale-up often stalls: the operator rebalances right after `add-node`, the first `MIGRATE` fails
with `CLUSTERDOWN`, and slots stay open while the operator just requeues. After 30 s of open slots
the script runs `valkey-cli --cluster fix` and `--cluster rebalance --cluster-use-empty-masters`
itself.

The operator keeps the PVCs of removed pods. A later scale-up would start those pods on the old
`nodes.conf` and break, so scale-down deletes them.

## Failover

[`failover.sh`](failover.sh) writes 30 keys and deletes the pod of a master. The operator's preStop
hook runs `CLUSTER FAILOVER` on its replica, so promotion takes about 2 s and all keys read back.
The recreated pod keeps its node ID and rejoins as a replica.

With `HARD=1` the master is frozen instead, so the other nodes only fail it over after
`cluster-node-timeout` (15 s), about 20 s in total. The frozen pod is then force-deleted.

## Benchmark

`make bench` runs the same two benchmarks as the other Valkey and Dragonfly examples, with the same
settings: `BENCH_N=200000` operations, `BENCH_C=50` clients, `BENCH_KEYS=100000` keys. Each runs in
its own pod (`kubectl run --rm`) and starts from Service `valkey-leader`.

`make bench-resp` is `valkey-benchmark --cluster -t set,get,incr,lpush,hset` from a
`valkey/valkey:9.1.2-alpine` pod, at `-P 1` and `-P 16`. In cluster mode it only notices that a
test is done on a 250 ms timer, so ops/s is `BENCH_N` divided by a multiple of 0.25 s (800000,
400000, 266667, ...); latencies are not affected.

`make bench-lua` runs [`go/rueidis-lua-bench`](../../go/rueidis-lua-bench), linked as `bench`:
SET, GET and three Lua scripts through rueidis, which sees a cluster and sends each key to its
leader. The image is built locally and loaded with `kind load docker-image`.

Resources are left as in [`rediscluster.yaml`](rediscluster.yaml): each pod gets 500m CPU and
256Mi. All six pods and the bench pod share one kind node.

Close to [`valkey-operator`](../valkey-operator) on the same kind node (within about 25%). There is no sharded Dragonfly on kind to compare with: the Dragonfly operator runs one primary with replicas.

Results from `BENCH_N=1000000 make bench` on an Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03, one setup at a time:

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
