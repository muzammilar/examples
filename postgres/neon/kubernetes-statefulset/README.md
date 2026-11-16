# Neon: native storage components on Kubernetes

Dedicated kind StatefulSets run one pageserver, one storage broker, three
safekeepers and a PostgreSQL compute endpoint. A branch compute uses an ancestor
LSN. This is a **StatefulSet fallback**, not an official Neon operator or managed
Neon HA/control-plane deployment. The workload contains **1,000,000 orders per
backend**, including a three-copy PostgreSQL comparison outside Kubernetes.

## Quick start

Prerequisites: Docker, Make, Python 3, kind and kubectl. Allow 16 GiB Docker RAM
and at least 30 GiB free Docker disk for imported images and PVCs, with additional
room for existing images. Each example owns its cluster and support services.

1. `make kind-up` — build the workload, start owned S3/PostgreSQL dependencies, create kind and import role-specific images.
2. `make up` — install native storage/compute StatefulSets and seed one million orders per backend.
3. `make test` — verify complete values, PostgreSQL replicas and every historical acknowledged write.
4. `make down` — drop workload data and remove the owned Kubernetes namespace.
5. `make kind-down` — delete the cluster/private context and owned support services, volumes and images.

Cluster **`neon-storage`**, context **`kind-neon-storage`**, kubeconfig
`results/kubeconfig`. Make exports this file before kind creation; every kubectl
call specifies the context. There is one control plane and two workers. Safekeepers
are placed one per node; compute, pageserver and workload remain on the control
plane, with the branch on the second worker. Results/witness files are mounted
outside the database PVCs. Imported images match this fixed placement.

## Components and comparison

| Component | Version / configuration |
|---|---|
| Kubernetes | kind node v1.34.0; private three-node cluster |
| Neon storage | `ghcr.io/neondatabase/neon@sha256:7a4f124917bb929964b2d696d710f19584f80bb9bd51b2af4a6e2425434c761f` |
| Compute | `ghcr.io/neondatabase/compute-node-v17@sha256:13ab146d3e7bbabb25a8532f315ac443e7512351d1ede0bab586def5c70e26c3`; PostgreSQL 17.5 snapshot |
| Pageserver | 2 CPUs / 2 GiB; one instance, emergency control plane; 2 GiB local PVC |
| Safekeepers × 3 | 1 CPU / 512 MiB each; peer recovery and S3 remote WAL, 2 GiB PVC each |
| Compute / branch | 2 CPUs / 2 GiB each; 2 GiB local cache PVC, ephemeral `/tmp` |
| Workload | Go 1.25.4 + pgx; 2 CPUs / 2 GiB; Python/psycopg controls native branching |
| PostgreSQL comparison | 17.6 primary: 2 CPUs / 2 GiB; two replicas: 1 CPU / 1 GiB each; synchronous ANY 1 and permanent slots |
| Garage S3 | 2.5.0, nine peers, RF3; 1 CPU / 768 MiB per peer; gateway 500m CPU / 128 MiB |

The pinned self-hosted snapshot is intentionally reproducible and does not claim
current production patch currency. Support endpoints are generated from owned,
label-verified Docker bridge addresses; only native Neon components are in kind.
No PostgreSQL, Garage or Neon Kubernetes operator is being qualified. Aggregate
CPU limits oversubscribe the shared 11-CPU VM. Native storage and object-store
costs differ from PostgreSQL's replicated full-data copies; these are architecture
observations, not equal-budget capacity rankings. The importer reserves 8 GiB of
Docker disk beyond estimated missing images and stops if host free disk falls
below 5 GiB. WAL-only nodes import a small image containing the unchanged pinned
safekeeper executable and its upstream runtime libraries. Storage/compute images
use stable local tags derived from their original pinned digests.

## Experiments

`make benchmark` measures COPY ingestion and validated SQL queries on matching
order bodies. `make branch` checks ancestor-LSN contents and branch isolation,
separately measuring native branch API time and usable endpoint time; full
PostgreSQL logical copy has different semantics and is not a like-for-like COW
storage-space comparison.

`make test-production` runs correctness, branch and the following independent cases:

| Target | Intended injection and recovery |
|---|---|
| `make failover` | Kill a safekeeper process via the actual owned CRI/containerd task and verify runtime exit and native restart. |
| `make disk-failure` | Fence one safekeeper, remove its exact owned PVC, hold 30 seconds, recreate and verify native peer-WAL recovery. |
| `make chaos-node` | SIGKILL the worker owning sk1; require NotReady during a 30-second hold, then restart the retained worker. |
| `make chaos-compute` | Crash the compute process and verify restart using retained cache and remote durable state. |
| `make compute-replacement` | Fence compute, replace its local cache PVC and recover from storage components. |

Writers and independent known-order readers remain active through faults and native
repair. External fsynced journals retain attempted, acknowledged and ambiguous
COPY batches; no mutation replay hides uncertainty. Native safekeeper flush/commit
LSNs must pass a frozen acknowledged-write barrier on all three members before
fresh post-repair progress. Full history reconciliation checks complete bodies,
missing/unexpected rows and atomic COPY outcomes. Phase reports separate failures
and successful-request latencies; immutable reader samples do not establish
fresh-write visibility lag.

The November 15 Kubernetes results predate the later COPY percentile correction
and explicit application-side write deadline in the shared Go workload. Those
follow-ups were revalidated in both Compose shapes on November 16; this Kubernetes
fault matrix was not repeated for that follow-up.

## Measured results

2026-11-15, Apple M4 Pro, native ARM64 Docker, shared 11-CPU / 24.42 GiB VM.
The full lifecycle and owned cleanup passed. One million order bodies were checked
on both primaries, both PostgreSQL replicas and the Neon branch. Query samples
use one warmup then 20 timed executions; p99 is the maximum of this small sample.
The Go producer uses 100 × 10,000-row COPY transactions per backend, with index
maintenance, body generation and fsynced witness records inside the elapsed load
interval; connection/schema creation is excluded. There were zero ingestion errors.

| Backend | COPY rows/s | Batch p50 / p99 ms | Acknowledged rows |
|---|---:|---:|---:|
| neon | 247,848.5 | 30.375 / 314.055 | 1,000,000 |
| postgres | 411,838.8 | 18.277 / 85.385 | 1,000,000 |

| Query | Neon p50 / p99 ms | PostgreSQL p50 / p99 ms |
|---|---:|---:|
| customer-orders | 0.096 / 0.395 | 0.098 / 0.146 |
| all-orders | 27.300 / 38.434 | 17.150 / 48.677 |

Branch API creation took **4.917 ms**; usable branch SQL,
including deployment, endpoint startup and readiness polling, took
**6.291 s**. Full PostgreSQL pg_dump/pg_restore
took **3.503 s**, excluding database creation, with a
**46,663,913-byte** dump. These are different operations, not a COW
speed ratio. Full branch bodies and parent isolation passed, and the final test
read all one million branch bodies again after the storage faults.

The five passing cases below retain live writers and independent readers through
native recovery. Elapsed time includes final full SQL/history reconciliation;
node/PVC cases additionally include their deliberate 30-second holds. Node time
also includes waiting for Kubernetes to observe the missing heartbeat. Per-phase
request counts and separate success/failure p50/p99 are in each observation file.

| Case | Elapsed s | ACK rows in case | Ambiguous requests | Read errors | Missing/wrong known reads |
|---|---:|---:|---:|---:|---|
| compute-cache | 42.974 | 3,300 | 11 | 11 | 0 / 0 |
| compute-process | 7.877 | 3,100 | 3 | 2 | 0 / 0 |
| node | 99.556 | 89,600 | 0 | 0 | 0 / 0 |
| wal-disk | 42.876 | 37,400 | 0 | 0 | 0 / 0 |
| wal-process | 21.221 | 17,600 | 0 | 0 | 0 / 0 |

Final reconciliation covers **300,400 acknowledged rows across eight journals**,
including the three failed controller attempts: **zero missing ACKs, wrong or
duplicate values, and partial COPY transactions**. Failed attempts remain failures;
their surviving data does not turn them into successful operational tests. The
following worker outage required writes through the rebuilt safekeeper plus the
other survivor, demonstrating the replacement was usable. Native flush/commit LSNs
passed fixed external-ACK barriers on all three safekeepers before injection and
after repair in the corrected controller. Raw evidence is preserved under `results/archive-20261115T231528.620208/`.
Raw LSNs are not an independent hash of
every WAL byte. Existing successful process reports predate the added pre-injection
barrier, while the disk/node/compute reruns exercise it.

## Gotchas and recovery limits

- Initial image import exhausted Docker's disk, reported a read-only containerd
  filesystem, and Docker became unresponsive. Diagnostics were preserved. The
  still-empty kind cluster was removed after restart; approved unused-build-cache
  cleanup reclaimed capacity. No populated workload was reset. Import capacity
  guards and role-specific images now prevent that setup from silently consuming
  the remaining shared disk.
- An older completed lifecycle left its generated Garage identity marker after
  deleting the actual volumes. Teardown now removes that generated directory;
  archival also preserves/removes old generated support state only after proving
  successful cleanup and absence of owned containers, volumes and kind nodes.
  Never discard a live cluster's identity marker to force bootstrap to succeed.
- Digest-only exports produced transient `import-...` aliases. containerd/CRI then
  rejected broker/pageserver with a missing checkpoint-image reference. Stable
  local names derived from the pinned source digests fixed bootstrap; the original
  image content remains pinned. Both bootstrap failures are preserved in results.
- TCP/Pod readiness preceded authenticated SQL through the workload's Service
  path. `wait_sql.py` now waits for SELECT 1 before COPY and branch verification.
- A restarted safekeeper's status/recovery HTTP service also briefly refused
  connections. Native polling now tolerates transient errors while keeping the
  load live. The ACK LSN target is frozen once, rather than chasing a moving writer.
- In the pinned upstream `pull_timeline` implementation an existing timeline is an
  idempotent success. Both successful disk-recovery responses had
  `safekeeper_host:null`: native timeline recreation/peer recovery had already
  started, so the manual call was a no-op. New PVC UID, three-copy LSN convergence,
  historical SQL readback and a subsequent different-worker outage establish the
  tested recovery; an API success alone does not.
- A hard-stopped worker reports Ready=Unknown (`NodeStatusUnknown`), not necessarily
  Ready=False. The old wait timed out; the corrected gate records either unavailable
  status before its hold. The failed attempt's acknowledged rows remain reconciled.
- Run branch creation once per fresh lifecycle. After a failed aggregate, resume
  the individual fault targets; branch creation deliberately refuses to replace an
  existing branch/copy. `make test` rechecks an existing branch without recreating it.
- The pinned snapshot/emergency control plane and one pageserver do not reproduce
  managed Neon HA. Storage-controller upcall warnings are expected in this limited
  topology; layer-GC/storage reclamation and managed control-plane behavior are not
  qualified. A branch is not an independent backup. All logical zones share a host.
- Unrun: pageserver/S3/broker outages, network partition, WAL-quorum loss, permanent
  worker rescheduling, scaling/PDB/drain tests, version upgrades, independent backup
  restore, pressure/soak and production authentication/TLS. Operator reconciliation
  cases are unsupported for this direct StatefulSet deployment. Volume replacement
  is not physical EIO or independent-host power-loss testing.

## Source and build

`orders-workload/` links to tracked repository source. `prepare.py` creates ignored
`.build/orders-workload/` using hard links/copies for the local Docker build.
Dependency definitions/controllers are local; no sibling Makefile or generated
state is required. Generated Kubernetes JSON, private kubeconfig and results are
ignored and never committed.

Sources: [Neon architecture](https://neon.com/docs/introduction/architecture-overview),
[pinned upstream source](https://github.com/neondatabase/neon/tree/77e22e4bf09d88b70b4a83a38c2f6de6301816b4),
[upstream self-hosted Compose](https://github.com/neondatabase/neon/tree/main/docker-compose),
[pinned idempotent timeline recovery](https://github.com/neondatabase/neon/blob/77e22e4bf09d88b70b4a83a38c2f6de6301816b4/safekeeper/src/pull_timeline.rs).
