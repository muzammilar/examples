# TigerBeetle — 3-replica StatefulSet on kind

A plain [StatefulSet](tigerbeetle.yaml) of three TigerBeetle replicas on kind, one per worker /
zone, with a primary-kill failover test. Requires `kind`, `kubectl`, `docker`.

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

`up` = `kind-cluster` + `image` + `cluster`. `failover` takes `DURATION` and `KILL_AFTER`
(seconds).

## Why a plain StatefulSet

TigerBeetle has no Kubernetes operator or Helm chart. The
[deploying docs](https://docs.tigerbeetle.com/operating/deploying/) cover systemd, Docker and the
managed service; only [upgrading](https://docs.tigerbeetle.com/operating/upgrading/#upgrading-docker-based-installations)
mentions Kubernetes ("update the tag … have a rolling deploy strategy set up"). Rafiki's docs
point at a `tigerbeetle.github.io/helm-charts` repo that returns 404; the GitHub repos that exist
(`Code-Growers/tigerbeetle-operator`, "experimental", and a few personal Helm charts) have 0 stars
(checked 2026-10-03).

## How it is wired

- **Addresses.** `--addresses` takes IPs only, is read once at start, and must list every replica
  in replica order on every replica and client. Pod IPs change on reschedule, so each replica has
  a ClusterIP Service (`tigerbeetle-0..2`) with a fixed IP `10.96.53.10..12` selecting
  `statefulset.kubernetes.io/pod-name`; clients use those IPs. A replica must bind the address in
  its own slot, which a ClusterIP is not, so `start.sh` puts the pod IP (downward API) in its own
  slot and the Service IPs in the others. The headless Service `tigerbeetle` is only the
  StatefulSet's `serviceName`.
- **Startup.** `podManagementPolicy: Parallel` starts all three pods at once (with `OrderedReady`,
  `tigerbeetle-1` would wait for `tigerbeetle-0`). All Services set `publishNotReadyAddresses` so
  replicas reach each other before they are ready.
- **Format / recover.** The init container `format` takes the replica index from the pod ordinal
  (`${HOSTNAME##*-}`) and runs `tigerbeetle format --cluster=0 --replica=<i> --replica-count=3`
  when the PVC has no data file. Once all three are ready, `make up` sets `BOOTSTRAPPED=true` in
  the ConfigMap; after that a pod with no data file runs
  [`tigerbeetle recover`](https://docs.tigerbeetle.com/operating/recovering/) instead (a replica
  re-formatted in a live cluster could have forgotten votes and lose committed data). No target
  deletes a PVC, so this path was not run on kind.
- **Replica count.** Lives in the data files; `spec.replicas` must stay 3. A fourth pod would run
  `format --replica=3 --replica-count=3`, which TigerBeetle rejects (see
  [`../README.md`](../README.md#known-issues)).

| item | value |
|------|-------|
| security | `seccompProfile: Unconfined` (io_uring), `IPC_LOCK` |
| resources | request 3 GiB, limit 2 CPUs / 4 GiB; ~2.3 GiB used with `--cache-grid=256MiB` |
| PDB | one replica down at a time (2 of 3 are a quorum) |
| storage | 2 GiB PVCs, kind's `standard` (local-path) class |
| server image | `ghcr.io/tigerbeetle/tigerbeetle:0.17.9` |
| test client | `client/`, `python:3.13-slim` + `tigerbeetle==0.17.9`, built locally and loaded into kind (`imagePullPolicy: Never`) |

## Results

2026-10-03, Docker Desktop 29.5.3, Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64), kind
0.32.0 (Kubernetes 1.35), TigerBeetle 0.17.9, one run each. Everything shares one Docker VM and
disk: this measures the example, not TigerBeetle on dedicated machines.

| test | result |
|------|--------|
| `make up` | 3 pods ready 42 s after the apply (images already pulled); view 1, `tigerbeetle-1` primary. `make test` passed; the REPL in `tigerbeetle-0` reached the cluster through the Service IPs. |
| `make failover` | Load ~50k transfers/s (batches of 100, one client). Primary `tigerbeetle-2` force-deleted at 16:05:11; the other two in view 3 with `tigerbeetle-0` primary at 16:05:12.15. Longest request 314 ms, none failed. New pod got a new IP (10.244.3.3 → 10.244.3.4), ready (listening) 2 s after the kill, rejoined as backup 18 s after. 2,013,500 transfers acked in 40 s, all in the sink's balance. |
| `make failover` retest (18:20) | Primary `tigerbeetle-1` killed at 10 s; `tigerbeetle-2` primary of view 2 within a second, new pod ready 2 s after. The client then ran at ~2,800 transfers/s for ~10 s (logging `on_connect: error to=1 error.ConnectionTimedOut`) before returning to ~50k/s. Longest request 656 ms; all 1,342,400 acked transfers in the sink's balance. |
| `make benchmark` | 1M transfers, 10k accounts, 1 client, batches of 8,189; client 2 CPUs, replicas 2 CPUs / 4 GiB each: **346,813 transfers/s**, batch p50 15 ms / p99 55 ms. Within 10% of [Docker Compose](../docker-compose-cluster/README.md#benchmark) (381k/s): routing replica traffic through Service IPs cost little. |

## Known issues

- Slow client recovery after a primary kill (retest above): ~10 s at ~2,800 transfers/s with
  `on_connect: error to=1 error.ConnectionTimedOut` before full rate returned. No request failed.
- No operator/Helm chart, IP-only `--addresses`, fixed replica count: see
  [`../README.md`](../README.md#known-issues).
