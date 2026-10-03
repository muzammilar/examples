# Valkey: official Helm chart on kind, cluster on Docker Compose

The official [`valkey/valkey`](https://github.com/valkey-io/valkey-helm) chart `0.12.0` (Valkey `9.1.2`)
on kind as one primary and two replicas, and a 6-node Valkey cluster (3 primaries + 3 replicas) on
Docker Compose.

The chart only does standalone or primary/replica, with no cluster mode and no Sentinel. That's why
the cluster runs on Compose, and why nothing promotes a replica on kind.

```bash
make kind-up           # kind cluster valkey-helm, helm install valkey/valkey into namespace valkey
make kind-test         # roles, Services, 1000 writes + WAIT 2, reads on every pod
make kind-failover     # delete valkey-0, read from replicas, wait for it to come back as primary
make kind-status       # pods, Services, replication info per pod
make kind-bench        # kind-bench-resp, then kind-bench-lua, against Service valkey
make kind-cli          # valkey-cli on valkey-0
make kind-down         # delete the kind cluster

make compose-up        # six nodes, then valkey-cli --cluster create --cluster-replicas 1
make compose-test      # CLUSTER INFO on every node, topology, 1000 keys through valkey-cli -c
make compose-failover  # stop the primary of slot 0, its replica takes over, old primary rejoins
make compose-status    # CLUSTER INFO + CLUSTER NODES
make compose-bench     # compose-bench-resp, then compose-bench-lua, against the cluster
make compose-cli       # valkey-cli -c on valkey-1
make compose-down      # remove containers and volumes

make up test failover status bench down   # each runs the kind-* target, then the compose-* one
make bench-resp bench-lua                  # same, for one of the two benchmarks
```

## Tools

`direnv allow` (or `nix develop`) at the repo root gives you kind, kubectl, helm and valkey-cli; Docker
comes from the host. On Linux without Nix:

```bash
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker "$USER"
curl -Lo kind https://kind.sigs.k8s.io/dl/v0.32.0/kind-linux-amd64
curl -LO https://dl.k8s.io/release/v1.37.1/bin/linux/amd64/kubectl
curl -fsSL https://get.helm.sh/helm-v4.3.0-linux-amd64.tar.gz | tar xz --strip-components 1 linux-amd64/helm
sudo install -m 0755 kind kubectl helm /usr/local/bin/ && rm kind kubectl helm
```

## kind

[`values.yaml`](values.yaml) turns on 2 replicas with 1Gi PVCs and AOF. Service `valkey` points at
`valkey-0` only (writes), `valkey-read` at all three pods.

`make kind-test` checks that `valkey-0` is primary with 2 connected replicas and both replicas follow
it, that the Services point at the right pods, writes 1000 keys through `valkey` followed by
`WAIT 2` (both replicas must ack), reads them back on every pod and through `valkey-read`, and checks
that the replicas answer `READONLY` to writes.

`make kind-failover` deletes `valkey-0`. While it's gone the replicas still serve all 1000 keys and
writes through `valkey` fail. The StatefulSet recreates `valkey-0` on the same PVC, it loads its AOF
and is primary again, the replicas reconnect, and 100 new writes reach both. Without the PVC + AOF,
`valkey-0` would come back empty and the replicas would resync to that, dropping their data.

## Docker Compose

Six nodes `valkey-1..6` with cluster mode and AOF, one volume each, on `127.0.0.1:7101..7106`. Nodes
announce their hostname, so redirects name `valkey-N`, which only resolves inside the compose
network: use `make compose-cli` for `-c`.

`make compose-test` checks `CLUSTER INFO` on all six (state ok, 16384 slots, 6 nodes, 3 shards),
that there are 3 primaries with slots and one replica each, writes and reads 1000 keys through
`valkey-cli -c`, and waits until each replica has as many keys as its primary.

`make compose-failover` stops the primary of slot 0. Its replica is promoted within a few seconds
(node timeout is 5s), all keys are still readable and new writes work. The old primary is started
again and rejoins as a replica of the promoted node; it doesn't take the primary role back, so the
next run stops the other node of that pair.

## Benchmark

`make bench` runs two benchmarks on kind and then on Compose, with the same settings as every other
Valkey and Dragonfly example here: `BENCH_N=200000` operations, `BENCH_C=50` clients,
`BENCH_KEYS=100000` keys.

`bench-resp` is `valkey-benchmark -t set,get,incr,lpush,hset` from a `valkey/valkey:9.1.2-alpine`
container, at `-P 1` and `-P 16`. `bench-lua` is
[`go/rueidis-lua-bench`](../../go/rueidis-lua-bench), linked as `bench`: SET, GET and three Lua
scripts through rueidis.

On kind both run as a pod (`kubectl run --rm`) against Service `valkey`, so only `valkey-0` takes
the load. The Go image is built locally and loaded with `kind load docker-image`. The chart sets no
resource limits; the pods share the kind node with everything else.

On Compose both run in the compose network against the cluster: `valkey-benchmark --cluster`, and
rueidis, which sees a cluster node and sends each key to its primary. Every node is limited to 1 CPU
and 512 MiB, the same as [`dragonfly/docker-compose-cluster`](../../dragonfly/docker-compose-cluster),
and Valkey runs commands on one thread. AOF stays on (`appendfsync everysec`).

In `--cluster` mode `valkey-benchmark` only notices that a test is done on a 250 ms timer, so ops/s
is `BENCH_N` divided by a multiple of 0.25 s (800000, 400000, 266667, ...). Latencies are not
affected. Raise `BENCH_N` if you want finer steps.

The Compose cluster beats [`dragonfly/docker-compose-cluster`](../../dragonfly/docker-compose-cluster) at 1 CPU per node on every test but the Lua bench's GET, which is about even: 1.5-4x on most, up to 5.5x on pipelined SET, and RESP p99 under 1 ms vs 5-18 ms. On kind the Valkey primary runs `add.lua` at 364k ops/s against 149k for [`dragonfly/helm-chart`](../../dragonfly/helm-chart), though pod resources differ.

Results from `BENCH_N=1000000 make kind-bench` / `make compose-bench` on an Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03, one setup at a time:

kind (primary + 2 replicas, writes to Service `valkey`):

`valkey-benchmark` (RESP), 50 clients, 100k keys:

| op | ops/s | p50 ms | p99 ms | ops/s `-P 16` | p50 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| SET | 270,490 | 0.14 | 0.27 | 1,366,120 | 0.53 | 0.81 |
| GET | 320,924 | 0.08 | 0.15 | 3,134,796 | 0.21 | 0.32 |
| INCR | 272,480 | 0.14 | 0.26 | 1,639,344 | 0.44 | 0.70 |
| LPUSH | 269,760 | 0.14 | 0.25 | 1,438,849 | 0.52 | 0.72 |
| HSET | 275,482 | 0.14 | 0.27 | 1,148,106 | 0.66 | 1.02 |

`bench-lua` (Go, rueidis, standalone, 1 node), 50 workers, 100k keys:

| op | ops/s | p50 ms | p99 ms |
|---|---:|---:|---:|
| SET | 376,271 | 0.13 | 0.25 |
| GET | 551,345 | 0.09 | 0.19 |
| add.lua | 363,872 | 0.14 | 0.28 |
| update.lua | 220,430 | 0.23 | 0.39 |
| delete.lua | 381,382 | 0.13 | 0.26 |

Compose cluster (3 primaries + 3 replicas, 1 CPU per node):

`valkey-benchmark` (RESP), 50 clients, 100k keys:

| op | ops/s | p50 ms | p99 ms | ops/s `-P 16` | p50 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| SET | 444,247 | 0.06 | 0.15 | 1,996,008 | 0.24 | 0.64 |
| GET | 499,750 | 0.06 | 0.11 | 1,996,008 | 0.11 | 0.27 |
| INCR | 399,680 | 0.08 | 0.16 | 2,000,000 | 0.22 | 0.49 |
| LPUSH | 444,444 | 0.07 | 0.15 | 1,996,008 | 0.21 | 0.53 |
| HSET | 363,504 | 0.09 | 0.18 | 1,329,787 | 0.36 | 0.84 |

`bench-lua` (Go, rueidis, cluster, 6 nodes), 50 workers, 100k keys:

| op | ops/s | p50 ms | p99 ms |
|---|---:|---:|---:|
| SET | 422,124 | 0.11 | 0.29 |
| GET | 460,957 | 0.10 | 0.27 |
| add.lua | 446,089 | 0.10 | 0.26 |
| update.lua | 315,508 | 0.13 | 0.33 |
| delete.lua | 461,261 | 0.10 | 0.25 |
