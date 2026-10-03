# Dragonfly: primary + 2 replicas on kind with the Dragonfly Operator

[Dragonfly Operator](https://github.com/dragonflydb/dragonfly-operator) `v1.7.0` (Helm chart from
`oci://ghcr.io/dragonflydb/dragonfly-operator/helm`) managing a `Dragonfly`
([`dragonfly.yaml`](dragonfly.yaml)) that runs Dragonfly `v2.0.0`: `replicas: 3`, so one master and
two replicas, one proactor thread and `--maxmemory=512mb` each, a snapshot every minute to a 1Gi PVC
per pod.

The API is `dragonflydb.io/v1alpha1`. The operator only does primary/replica replication, there is
no Dragonfly cluster mode or sharding: every pod holds the whole dataset.

Requires `kind`, `kubectl`, `helm`. `direnv allow` or `nix develop` at the repo root provides them.
On Linux:

```bash
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
curl -fsSLo kind https://kind.sigs.k8s.io/dl/v0.32.0/kind-linux-$ARCH && sudo install kind /usr/local/bin/
curl -fsSLO https://dl.k8s.io/release/v1.37.1/bin/linux/$ARCH/kubectl && sudo install kubectl /usr/local/bin/
curl -fsSL https://get.helm.sh/helm-v4.3.0-linux-$ARCH.tar.gz | tar xz && sudo install linux-$ARCH/helm /usr/local/bin/
```

```bash
make kind-up      # create the kind cluster only
make up           # kind cluster, operator, Dragonfly; waits for master + 2 replicas
make test         # SET/GET via the Service, replicas have the keys and reject writes, EVAL
make persistence  # delete every pod, then SIGKILL every dragonfly; keys come back from snapshots
make scale-up     # spec.replicas +1 (or REPLICAS=N), then make test
make scale-down   # spec.replicas -1 (or REPLICAS=N), then make test
make failover     # delete the master pod, time the promotion
make status       # Dragonfly, pods with their role label, PVCs, INFO replication
make bench        # bench-resp, then bench-lua, from pods against Service dragonfly
make cli          # redis-cli on the master
make down         # delete the kind cluster
```

The operator labels each pod `role=master` or `role=replica`, and the `dragonfly` Service selects
`role=master`, so clients always write to the master. [`wait-ready.sh`](wait-ready.sh) asks
Dragonfly itself (`INFO replication`) and checks the labels and the Service endpoint:
`status.phase` turns `Ready` as soon as the first pod is up, before any replica exists.
The image has `redis-cli`, so the targets exec into the pods.

## Test

[`test.sh`](test.sh) writes 100 keys through the Service, reads them on every replica, checks a
`SET` on a replica fails with `READONLY You can't write against a read only replica.`, and runs two
`EVAL` scripts on the master.

## Persistence

Each pod snapshots to its own PVC every minute (`snapshot.cron`), `--dbfilename=dump` so each
snapshot overwrites the last one. [`persistence.sh`](persistence.sh) does two things:

1. Writes 1000 keys and deletes all pods right away. Dragonfly writes a snapshot on `SIGTERM`, so
   all keys come back, about 50 s later.
2. Writes 1000 keys, waits for the next cron snapshot, writes 100 more and `SIGKILL`s every
   dragonfly process on the kind node. The containers restart in place within about 15 s; the
   1000 keys are there, the 100 written after the snapshot are gone.

After both, whichever pod the operator makes master loads its own snapshot and the others resync
from it.

## Scaling

[`scale.sh`](scale.sh) patches `spec.replicas` (all pods, the master included). The new pod gets a
PVC, the operator makes it a replica of the current master, and it is synced in about 20 s.
Scaling down removes the highest ordinal and takes a couple of seconds; its PVC stays and is
reused by the next scale-up.

## Failover

[`failover.sh`](failover.sh) writes 1000 keys and deletes the master pod. The operator does the
promotion, not Dragonfly: it sees the master pod go, picks the ready replica with the highest
replication offset, runs `REPLICAOF NO ONE` on it, points the other replica at it and moves the
`role=master` label, so the Service follows. That took 1 to 2 s.

kube-proxy needs a few more seconds to move the Service. A connection opened in that window goes
to the dead pod and hangs in TCP SYN retries (once for over two minutes), so the script retries the
first write with a 2 s timeout. It went through about 4 s after the delete, and all 1000 keys read
back. The StatefulSet recreates the deleted pod, which comes up without a role label; the operator
makes it a replica of the new master about 15 s later.

## Benchmark

`make bench` runs the same two benchmarks as the other Valkey and Dragonfly examples, with the same
settings: `BENCH_N=200000` operations, `BENCH_C=50` clients, `BENCH_KEYS=100000` keys. Each runs in
its own pod (`kubectl run --rm`) against Service `dragonfly`, so only the master takes the load.

`make bench-resp` is `valkey-benchmark -t set,get,incr,lpush,hset` from a
`valkey/valkey:9.1.2-alpine` pod, at `-P 1` and `-P 16`. It prints `WARNING: Could not fetch
server CONFIG` because Dragonfly doesn't answer its `CONFIG GET`; the numbers are fine.

`make bench-lua` runs [`go/rueidis-lua-bench`](../../go/rueidis-lua-bench), linked as `bench`:
SET, GET and three Lua scripts through rueidis.
The image is built locally and loaded with `kind load docker-image`.

Resources are left as in [`dragonfly.yaml`](dragonfly.yaml): `--proactor_threads=1`,
`--maxmemory=512mb`, a 768Mi memory limit and no CPU limit, and the snapshot every minute keeps
running during the bench. All three pods and the bench pod share one kind node.

Against the Valkey primary in [`valkey/helm-chart`](../../valkey/helm-chart) on kind, unpipelined GET is about even and SET/INCR are 20-45% slower; the Lua scripts are slower too (`update.lua` 122k vs 220k ops/s) and pipelined writes much slower (SET `-P 16` 395k vs 1.37M); pod resources differ, so treat it as rough.

Results from `BENCH_N=1000000 make bench` on an Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03, one setup at a time:

`valkey-benchmark` (RESP), 50 clients, 100k keys:

| op | ops/s | p50 ms | p99 ms | ops/s `-P 16` | p50 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| SET | 220,264 | 0.18 | 1.09 | 395,101 | 1.32 | 6.36 |
| GET | 334,001 | 0.08 | 0.14 | 1,479,290 | 0.49 | 0.69 |
| INCR | 154,369 | 0.19 | 3.01 | 365,497 | 1.41 | 6.79 |
| LPUSH | 229,410 | 0.17 | 0.91 | 492,854 | 1.04 | 5.71 |
| HSET | 198,689 | 0.19 | 1.46 | 369,959 | 1.44 | 6.62 |

`bench-lua` (Go, rueidis, standalone, 1 node), 50 workers, 100k keys:

| op | ops/s | p50 ms | p99 ms |
|---|---:|---:|---:|
| SET | 251,654 | 0.17 | 1.08 |
| GET | 473,901 | 0.11 | 0.21 |
| add.lua | 253,241 | 0.19 | 0.49 |
| update.lua | 122,219 | 0.38 | 1.18 |
| delete.lua | 261,775 | 0.19 | 0.47 |
