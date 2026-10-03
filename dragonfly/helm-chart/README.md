# Dragonfly: official Helm chart on kind

The official Dragonfly chart (`oci://ghcr.io/dragonflydb/dragonfly/helm/dragonfly`) `v2.0.0`
(Dragonfly `v2.0.0`) on kind as one primary and two replicas, each with a 1Gi PVC and a snapshot
every minute.

The chart has no replication setting, no Sentinel and no automatic failover. `replicaCount: 3` alone
gives three unrelated Dragonflys behind one Service. [`values.yaml`](values.yaml) overrides the
container command so every pod except `dragonfly-0` starts with `--replicaof
dragonfly-0.dragonfly:6379`. `cluster.mode` only passes `--cluster_mode` to each pod; nothing
assigns slots or joins nodes, so this example doesn't use it. For managed failover use the
[Dragonfly Operator](https://github.com/dragonflydb/dragonfly-operator).

Requires kind, kubectl, helm (`direnv allow` or `nix develop` at the repo root provides them). On
Linux without Nix:

```bash
curl -Lo kind https://kind.sigs.k8s.io/dl/v0.32.0/kind-linux-amd64
curl -LO https://dl.k8s.io/release/v1.37.1/bin/linux/amd64/kubectl
curl -fsSL https://get.helm.sh/helm-v4.3.0-linux-amd64.tar.gz | tar xz --strip-components 1 linux-amd64/helm
sudo install -m 0755 kind kubectl helm /usr/local/bin/ && rm kind kubectl helm
```

```bash
make up        # kind cluster dragonfly-helm, helm install into namespace dragonfly
make test      # replication roles, 1000 writes read back on every pod, EVAL, INFO
make failover  # delete dragonfly-0, read from replicas, it comes back from its snapshot
make status    # pods, Services, PVCs, replication and last snapshot
make bench     # bench-resp, then bench-lua, from pods against dragonfly-primary
make cli       # redis-cli on dragonfly-0
make down      # delete the kind cluster
```

Each pod runs with `--proactor_threads=2 --maxmemory=512mb` and saves `/data/dump*` on
`--snapshot_cron="* * * * *"`. The chart's Service `dragonfly` is made headless (`clusterIP: None`)
so `dragonfly-0.dragonfly` resolves; it lists all three pods. Writes go through
`dragonfly-primary`, an extra Service on `dragonfly-0` only.

## Test

`make test` checks that `dragonfly-0` is primary with 2 connected replicas, writes 1000 keys through
`dragonfly-primary`, reads them back on every pod, checks that the replicas reject writes, runs a
Lua `EVAL` that does `INCRBY`, and prints a few `INFO` fields.

## Failover

`make failover` writes 1000 keys, then 100 more right before it deletes `dragonfly-0`. While it's
gone the replicas still serve reads and stay replicas: nothing promotes one, so there is no primary.
The StatefulSet recreates `dragonfly-0` on its PVC a few seconds later. Its log shows
`Loading /data/dump-summary.dfs` and `Load finished, num keys read: 1101`, and it is primary again;
the replicas reconnect and resync, and 100 new writes reach both.

The last 100 keys survive because Dragonfly saves a snapshot when it gets SIGTERM, not because of
the cron. The minute cron only covers a crash or OOM kill, where you lose what was written since
the last save. Without the PVC `dragonfly-0` would come back empty and the replicas would resync to
that, dropping their data.

## Benchmark

`make bench` runs the same two benchmarks as the other Valkey and Dragonfly examples, with the same
settings: `BENCH_N=200000` operations, `BENCH_C=50` clients, `BENCH_KEYS=100000` keys. Each runs in
its own pod (`kubectl run --rm`) against Service `dragonfly-primary`, so only `dragonfly-0` takes
the load.

`make bench-resp` is `valkey-benchmark -t set,get,incr,lpush,hset` from a
`valkey/valkey:9.1.2-alpine` pod, at `-P 1` and `-P 16`. It prints `WARNING: Could not fetch
server CONFIG` because Dragonfly doesn't answer its `CONFIG GET`; the numbers are fine.

`make bench-lua` runs [`go/rueidis-lua-bench`](../../go/rueidis-lua-bench), linked as `bench`:
SET, GET and three Lua scripts through rueidis.
The image is built locally and loaded with `kind load docker-image`.

Resources are left as in [`values.yaml`](values.yaml): `--proactor_threads=2`,
`--maxmemory=512mb`, a 768Mi memory limit and no CPU limit, and the snapshot every minute keeps
running during the bench. All three pods and the bench pod share one kind node.

Dragonfly doesn't fly with Lua here either: the Valkey primary in [`valkey/helm-chart`](../../valkey/helm-chart) on kind runs `add.lua` 2.4x and `update.lua` 2.5x faster, and pipelined SET 1.8x; pod resources differ, so treat it as rough.

Results from `BENCH_N=1000000 make bench` on an Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03, one setup at a time:

`valkey-benchmark` (RESP), 50 clients, 100k keys:

| op | ops/s | p50 ms | p99 ms | ops/s `-P 16` | p50 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| SET | 273,075 | 0.12 | 0.26 | 755,287 | 0.90 | 3.34 |
| GET | 256,016 | 0.10 | 0.21 | 2,421,308 | 0.25 | 0.65 |
| INCR | 244,439 | 0.13 | 0.85 | 708,215 | 0.95 | 3.59 |
| LPUSH | 264,550 | 0.13 | 0.36 | 558,972 | 0.30 | 9.31 |
| HSET | 228,310 | 0.17 | 0.35 | 389,408 | 0.46 | 13.18 |

`bench-lua` (Go, rueidis, standalone, 1 node), 50 workers, 100k keys:

| op | ops/s | p50 ms | p99 ms |
|---|---:|---:|---:|
| SET | 230,585 | 0.21 | 0.49 |
| GET | 334,558 | 0.14 | 0.33 |
| add.lua | 148,641 | 0.30 | 1.07 |
| update.lua | 86,837 | 0.50 | 1.79 |
| delete.lua | 145,663 | 0.30 | 1.14 |
