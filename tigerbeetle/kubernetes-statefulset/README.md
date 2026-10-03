# TigerBeetle — 3-replica StatefulSet on kind

A plain StatefulSet of three TigerBeetle replicas on kind, one per worker / zone, with a
primary-kill failover test. Requires `kind`, `kubectl`, `docker`.

## Quick start

```bash
make up         # kind (3 workers in zones az-1..3), client image, StatefulSet; waits for 3 ready pods
make test       # Job demo: client/demo.py against the cluster, then lookup_accounts via the REPL
make failover   # Job load (40 s), kill the primary's pod after 10 s; prints the stall and checks every acked transfer
make benchmark  # Job bench: tigerbeetle benchmark, 1M transfers (SMOKE=1: 100k)
make status     # pods, nodes, current primary, each replica's last view change
make cli        # interactive tigerbeetle repl
make down       # delete the kind cluster
```

TigerBeetle has no Kubernetes operator or Helm chart of its own: the
[deploying docs](https://docs.tigerbeetle.com/operating/deploying/) cover systemd, Docker and the
managed service, and only [upgrading](https://docs.tigerbeetle.com/operating/upgrading/#upgrading-docker-based-installations)
mentions Kubernetes ("update the tag … have a rolling deploy strategy set up"). Rafiki's docs
still point at a `tigerbeetle.github.io/helm-charts` repo that no longer exists (404); the GitHub
repos that do exist (`Code-Growers/tigerbeetle-operator`, "experimental", and a few personal Helm
charts) have 0 stars (checked 2026-10-03). So this is a plain
[StatefulSet](tigerbeetle.yaml) of three replicas, one per kind worker / zone.

`up` is split into `kind-cluster`, `image`, `cluster`. `failover` takes `DURATION` and
`KILL_AFTER` (seconds).

How it is wired:

- `--addresses` takes IPs only (no DNS names), is read once at start, and must list every
  replica in replica order on every replica and client. Pod IPs change on every reschedule, so
  each replica has its own ClusterIP Service (`tigerbeetle-0..2`) with a fixed IP
  `10.96.53.10..12` selecting `statefulset.kubernetes.io/pod-name`. Clients use those three
  IPs. A replica binds the address in its own slot, which a ClusterIP is not, so `start.sh`
  puts the pod IP (downward API) in its own slot and the Service IPs in the others. The
  headless Service `tigerbeetle` is only the StatefulSet's `serviceName`.
- `podManagementPolicy: Parallel`: all three pods start at once. With the default
  `OrderedReady`, `tigerbeetle-1` would wait for `tigerbeetle-0` to be ready. All Services set
  `publishNotReadyAddresses` so replicas can reach each other before they are ready.
- The init container `format` takes the replica index from the pod ordinal (`${HOSTNAME##*-}`)
  and runs `tigerbeetle format --cluster=0 --replica=<i> --replica-count=3` when the PVC has no
  data file. After all three are ready, `make up` sets `BOOTSTRAPPED=true` in the ConfigMap. From
  then on a pod that finds no data file runs
  [`tigerbeetle recover`](https://docs.tigerbeetle.com/operating/recovering/) instead: a
  replica re-formatted in a live cluster could have forgotten votes it gave and lose committed
  data. No target here deletes a PVC, so this path was not run on kind.
- The replica count lives in the data files. `spec.replicas` must stay 3: a fourth pod would
  run `format --replica=3 --replica-count=3`, which TigerBeetle rejects (see
  [`../README.md`](../README.md#known-issues)).
- Pods run with `seccompProfile: Unconfined` (io_uring) and `IPC_LOCK`, request 3 GiB and are
  limited to 2 CPUs / 4 GiB. With `--cache-grid=256MiB` a replica takes ~2.3 GiB. The PDB allows
  one replica down at a time (2 of 3 are a quorum). PVCs are 2 GiB on kind's `standard`
  (local-path) class.
- The test client (`client/`, `python:3.13-slim` + `tigerbeetle==0.17.9`) is built locally and
  loaded into kind (`imagePullPolicy: Never`). Server image `ghcr.io/tigerbeetle/tigerbeetle:0.17.9`.

## Results

2026-10-03, Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64),
kind 0.32.0 (Kubernetes 1.35), TigerBeetle 0.17.9, one run each:

- `make up`: the three pods were ready 42 s after the apply (images already pulled), view 1
  with `tigerbeetle-1` as primary. `make test` passed, and the REPL in `tigerbeetle-0` reached
  the cluster through the Service IPs.
- `make failover`: the load client did ~50k transfers/s (batches of 100, one client). Its
  primary `tigerbeetle-2` was force-deleted at 16:05:11. The other two were in view 3 with
  `tigerbeetle-0` as primary at 16:05:12.15. The longest client request took 314 ms and no
  request failed. The new pod got a new IP (10.244.3.3 → 10.244.3.4), was ready (listening)
  2 s after the kill, and rejoined the view as a backup 18 s after the kill. 2,013,500
  transfers acked in 40 s, all of them in the sink's balance.
  A retest the same day (18:20) was slower to recover: primary `tigerbeetle-1` was killed at
  10 s, `tigerbeetle-2` was primary of view 2 within a second and the new pod was ready 2 s
  after the kill, but the client then ran at ~2,800 transfers/s for ~10 s (logging
  `on_connect: error to=1 error.ConnectionTimedOut`) before it was back at ~50k/s. No request
  took over 1 s (longest 656 ms) and all 1,342,400 acked transfers were in the sink's balance.
- `make benchmark` (1M transfers, 10k accounts, 1 client, batches of 8,189; client 2 CPUs,
  replicas 2 CPUs / 4 GiB each): **346,813 transfers/s**, batch p50 15 ms / p99 55 ms. That is
  within 10% of the same benchmark on Docker Compose
  ([`../docker-compose-cluster`](../docker-compose-cluster/README.md#benchmark), 381k/s):
  routing replica-to-replica traffic through the Service IPs did not cost much here.
  Everything shares one Docker VM and disk, so this measures the example, not TigerBeetle on
  dedicated machines.
