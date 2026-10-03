# Dragonfly: official Helm chart on kind

The official Dragonfly chart (`oci://ghcr.io/dragonflydb/dragonfly/helm/dragonfly`) `v2.0.0` (Dragonfly `v2.0.0`) on kind as one primary + two replicas, each with a 1Gi PVC and a snapshot every minute.

## Quick start

```bash
make up        # kind cluster dragonfly-helm, helm install into namespace dragonfly
make test      # replication roles, 1000 writes read back on every pod, EVAL, INFO
make failover  # delete dragonfly-0, read from replicas, it comes back from its snapshot
make status    # pods, Services, PVCs, replication and last snapshot
make bench     # bench-resp, then bench-lua, from pods against dragonfly-primary
make cli       # redis-cli on dragonfly-0
make down      # delete the kind cluster
```

Tools: kind, kubectl, helm (`direnv allow` or `nix develop` at the repo root). On Linux without Nix:

```bash
curl -Lo kind https://kind.sigs.k8s.io/dl/v0.32.0/kind-linux-amd64
curl -LO https://dl.k8s.io/release/v1.37.1/bin/linux/amd64/kubectl
curl -fsSL https://get.helm.sh/helm-v4.3.0-linux-amd64.tar.gz | tar xz --strip-components 1 linux-amd64/helm
sudo install -m 0755 kind kubectl helm /usr/local/bin/ && rm kind kubectl helm
```

## Setup

- Each pod: `--proactor_threads=2 --maxmemory=512mb`, saves `/data/dump*` on `--snapshot_cron="* * * * *"`.
- [`values.yaml`](values.yaml) overrides the container command so every pod except `dragonfly-0` starts with `--replicaof dragonfly-0.dragonfly:6379`.

| Service | Points at |
|---|---|
| `dragonfly` (chart's, made headless with `clusterIP: None` so `dragonfly-0.dragonfly` resolves) | all three pods |
| `dragonfly-primary` (extra) | `dragonfly-0` only (writes) |

- `make test`: `dragonfly-0` is primary with 2 connected replicas; 1000 keys written through `dragonfly-primary` and read back on every pod; replicas reject writes; a Lua `EVAL` doing `INCRBY`; a few `INFO` fields printed.
- `make failover`: writes 1000 keys, then 100 more right before deleting `dragonfly-0`. While it is gone the replicas serve reads and stay replicas: nothing promotes one, so there is no primary. The StatefulSet recreates `dragonfly-0` on its PVC a few seconds later; its log shows `Loading /data/dump-summary.dfs` and `Load finished, num keys read: 1101`, and it is primary again. The replicas reconnect and resync, and 100 new writes reach both.

The last 100 keys survive because Dragonfly saves a snapshot on SIGTERM, not because of the cron. The per-minute cron only covers a crash or OOM kill, where writes since the last save are lost. Without the PVC, `dragonfly-0` would come back empty and the replicas would resync to that, dropping their data.

## Benchmark

Same two benchmarks and settings as the other Valkey and Dragonfly examples: `BENCH_N=200000` operations, `BENCH_C=50` clients, `BENCH_KEYS=100000` keys. Each runs in its own pod (`kubectl run --rm`) against Service `dragonfly-primary`, so only `dragonfly-0` takes load.

| Target | What |
|---|---|
| `make bench-resp` | `valkey-benchmark -t set,get,incr,lpush,hset` from a `valkey/valkey:9.1.2-alpine` pod, at `-P 1` and `-P 16` |
| `make bench-lua` | [`go/rueidis-lua-bench`](../../go/rueidis-lua-bench) (linked as `bench`): SET, GET and three Lua scripts through rueidis. Image built locally and loaded with `kind load docker-image`. |

Resources as in [`values.yaml`](values.yaml): `--proactor_threads=2`, `--maxmemory=512mb`, 768Mi memory limit, no CPU limit; the per-minute snapshot keeps running during the bench. All three pods and the bench pod share one kind node.

Against the Valkey primary in [`valkey/helm-chart`](../../valkey/helm-chart) on kind (rough; pod resources differ): Valkey runs `add.lua` 2.4x, `update.lua` 2.5x and pipelined SET 1.8x faster.

`BENCH_N=1000000 make bench`, Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03, one setup at a time.

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

## Known issues

- **No replication setting, no Sentinel, no automatic failover in the chart.** `replicaCount: 3` alone gives three unrelated Dragonflys behind one Service. Workaround: the `--replicaof` command override above. For managed failover use the [Dragonfly Operator](https://github.com/dragonflydb/dragonfly-operator).
- **`cluster.mode` only passes `--cluster_mode` to each pod.** Nothing assigns slots or joins nodes, so this example doesn't use it.
- **`WARNING: Could not fetch server CONFIG`** from `valkey-benchmark`: Dragonfly doesn't answer its `CONFIG GET`. The numbers are fine.
