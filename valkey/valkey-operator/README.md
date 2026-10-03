# Valkey — 3-shard cluster on kind with Valkey Operator

[Valkey Operator](https://github.com/valkey-io/valkey-operator) `v0.7.1` (Helm chart `0.7.0`)
managing a `ValkeyCluster` ([`valkeycluster.yaml`](valkeycluster.yaml)): 3 shards x 1 replica,
Valkey `9.1.2`, AOF on a 1Gi PVC per node.

The official operator is early: its own README says it is not ready for production. Its API is
`v1alpha1`.

Requires `kind`, `kubectl`, `helm`. With Nix, run `direnv allow` (or `nix develop`) at the repo root.
On Linux (amd64):

```bash
curl -fsSLo kind https://kind.sigs.k8s.io/dl/v0.32.0/kind-linux-amd64
curl -fsSLo kubectl https://dl.k8s.io/release/v1.37.1/bin/linux/amd64/kubectl
curl -fsSL https://get.helm.sh/helm-v4.3.0-linux-amd64.tar.gz | tar -xz --strip-components=1 linux-amd64/helm
sudo install -m 0755 kind kubectl helm /usr/local/bin/ && rm kind kubectl helm
```

```bash
make kind-up      # create the kind cluster only
make up           # kind cluster, operator, ValkeyCluster; waits until healthy
make test         # cluster info, valkey-cli --cluster check, SET/GET 100 keys
make persistence  # write 1000 keys, delete every pod, read them back
make scale-up     # one more shard (or SHARDS=N)
make scale-down   # one shard fewer (or SHARDS=N)
make scale SHARDS=3 REPLICAS=2
make failover     # delete the primary of shard 0
make status       # ValkeyCluster, ValkeyNodes, pods, PVCs, keys and slots per primary
make bench        # bench-resp, then bench-lua, from pods against the cluster
make cli          # interactive valkey-cli -c
make down         # delete the kind cluster
```

[`wait-healthy.sh`](wait-healthy.sh) asks Valkey itself whether the cluster matches the spec
(`cluster_state:ok`, all slots, the right number of connected primaries and replicas). The
`ValkeyCluster` status is not enough: it stays `Ready` while every pod is being recreated.

The `server` container sets `VALKEYCLI_AUTH` to the operator user's password, so the targets run
`env -u VALKEYCLI_AUTH valkey-cli` to connect as the `default` user.

## Persistence

Each node keeps its AOF and `nodes.conf` on its PVC. `make persistence` deletes all six pods at
once; they come back with new IPs but the same node IDs and every key is still there, about 40 s
later. Some shards come back with primary and replica swapped, since a primary hands its slots to
its replica on `SIGTERM`.

Persistence can't be toggled after the cluster is created: the CRD rejects adding, removing or
shrinking `spec.persistence` (`make persistence` checks the removal). To change it, edit
`valkeycluster.yaml` and `make down up`.

## Scaling

`make scale-up` / `scale-down` patch `spec.shards` and wait until the cluster matches. The operator
moves slots with atomic slot migration (Valkey 9.0+), so keys stay readable throughout; 3 -> 4 -> 3
shards takes about 45 s each way. Don't change `spec.shards` again while slots are still moving.

## Failover

`make failover` deletes the shard 0 primary. Its replica is primary within a couple of seconds,
the keys read back from it, and the deleted pod returns on its PVC as a replica.
`cluster-node-timeout` is 5000 ms so an unclean primary loss fails over sooner.

## Benchmark

`make bench` runs the same two benchmarks as the other Valkey and Dragonfly examples, with the same
settings: `BENCH_N=200000` operations, `BENCH_C=50` clients, `BENCH_KEYS=100000` keys. Each runs in
its own pod (`kubectl run --rm`) and starts from the headless Service `valkey-valkey`.

`make bench-resp` is `valkey-benchmark --cluster -t set,get,incr,lpush,hset` from a
`valkey/valkey:9.1.2-alpine` pod, at `-P 1` and `-P 16`. In cluster mode it only notices that a
test is done on a 250 ms timer, so ops/s is `BENCH_N` divided by a multiple of 0.25 s (800000,
400000, 266667, ...); latencies are not affected.

`make bench-lua` runs [`go/rueidis-lua-bench`](../../go/rueidis-lua-bench), linked as `bench`:
SET, GET and three Lua scripts through rueidis, which sees a cluster and sends each key to its
primary. The image is built locally and loaded with `kind load docker-image`.

Resources are left as in [`valkeycluster.yaml`](valkeycluster.yaml): each node gets 500m CPU and
256Mi, `maxmemory 100mb` with `allkeys-lru`, AOF on. All six pods and the bench pod share one kind
node.

Close to [`ot-redis-operator`](../ot-redis-operator) on the same kind node (within about 25%). There is no sharded Dragonfly on kind to compare with: the Dragonfly operator runs one primary with replicas.

Results from `BENCH_N=1000000 make bench` on an Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03, one setup at a time:

`valkey-benchmark` (RESP), 50 clients, 100k keys:

| op | ops/s | p50 ms | p99 ms | ops/s `-P 16` | p50 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| SET | 234,577 | 0.07 | 0.17 | 1,331,558 | 0.22 | 1.01 |
| GET | 331,565 | 0.05 | 0.11 | 2,000,000 | 0.10 | 0.29 |
| INCR | 248,633 | 0.07 | 0.16 | 997,009 | 0.26 | 1.23 |
| LPUSH | 264,971 | 0.07 | 0.15 | 798,085 | 0.25 | 6.08 |
| HSET | 209,512 | 0.08 | 0.18 | 663,130 | 0.53 | 47.81 |

`bench-lua` (Go, rueidis, cluster, 6 nodes), 50 workers, 100k keys:

| op | ops/s | p50 ms | p99 ms |
|---|---:|---:|---:|
| SET | 269,256 | 0.11 | 0.31 |
| GET | 379,129 | 0.10 | 0.26 |
| add.lua | 268,417 | 0.10 | 0.44 |
| update.lua | 177,434 | 0.13 | 0.36 |
| delete.lua | 342,547 | 0.10 | 0.26 |
