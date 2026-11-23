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

`make test-dependencies` runs retained-cache pageserver outage, empty-cache
replacement, WAL-quorum loss and S3-gateway outage, followed by full verification.
`make backup-restore` is a separate logical application backup experiment; run
branch setup once (`make test-production`) first. It exports a consistent native
pg_dump snapshot while load continues, fences all original compute/storage
processes, restores into separate PostgreSQL storage, validates every snapshot
body, quantifies the excluded post-snapshot ACK tail and restores the source.
It does not restore the Neon control plane or promote a replacement Neon tenant.

## Measured results: November 16

Apple M4 Pro, native ARM64 Docker, shared **11 CPUs / 24.42 GiB**; budgets are
listed above. The latest shared Go workload includes corrected COPY percentiles
and an explicit write deadline. Three separate fixtures below retain their own
histories; their ACK counts are not summed across lifecycles. Both million-row
primaries, both PostgreSQL replicas and the million-row branch passed full body
verification. This is local architecture/recovery qualification, not independent-host
HA or a saturation benchmark.

### Final core fixture

A fresh full `test-production` invocation passed all five cases after the strict
fencing helper was applied to every process/PVC path. Six positive/negative fencing
controls also passed. Native exited-container states were recorded after process
kills; PVC deletion required absence from a healthy CRI. The subsequent worker
outage required the rebuilt safekeeper plus the other survivor to form quorum.

| Case | Recovery + full verification s | ACK rows | Ambiguous requests | Read errors |
|---|---:|---:|---:|---:|
| Safekeeper process | 8.846 | 5,600 | 0 | 0 |
| Safekeeper empty PVC | 45.028 | 37,800 | 0 | 0 |
| Worker outage | 95.556 | 84,500 | 0 | 0 |
| Compute process | 7.434 | 2,900 | 5 | 5 |
| Compute empty cache | 45.081 | 3,000 | 12 | 12 |

Final reconciliation: **133,800 ACK rows across five histories**, **0 missing ACKs,
0 wrong/duplicate bodies, 0 partial COPY transactions**. All three native WAL copies
passed fixed ACK-LSN barriers. Known-read bodies remained correct. PVC/node times
include deliberate 30-second holds; node time also includes heartbeat detection.

### Independent dependency/backup fixture

A separate fresh lifecycle validated pageserver/cache fencing, the persistent kind
worker address and actual read-only restore behavior, with independent writers and
readers throughout recovery.

| Case | Recovery + full verification s | ACK rows | Ambiguous requests | Read errors |
|---|---:|---:|---:|---:|
| Pageserver retained-cache outage | 43.866 | 3,100 | 3 | 3 |
| Pageserver empty-cache replacement | 141.411 | 14,500 | 13 | 3 |
| Worker restart with persistent kind IP | 104.009 | 89,800 | 0 | 0 |
| Independent logical restore, original source fenced | 24.461 | 6,700 | 6 | 6 |

Reconciliation: **114,100 ACK rows across four histories**, zero missing,
wrong/duplicate or partial results. Both pageserver cases proved native absence
from a healthy CRI. Empty-cache recovery retained the original tenant/timelines,
used a new PVC UID and higher attachment generation, and passed uploaded-LSN
barriers before removal and after recovery.

The independent restore verified **1,000,000 baseline + 110,300 snapshot recovery
rows**, including **108,700 ACKed recovery rows**. Its **51,969,627-byte** checksummed
backup excluded **3,900 ACK rows between snapshot and source fencing**; later ACKs
after source recovery are outside this RPO measurement. Snapshot rows also include
committed requests without a witnessed ACK. A new session reported read-only
defaults, and an UPDATE was rejected with SQLSTATE **25006**. The retained source
then rejoined and passed full historical verification. This is application-table
recovery into plain PostgreSQL, not physical Neon control-plane disaster recovery.

### Earlier dependency coverage

The earlier broader fixture also qualified the unchanged S3-gateway and WAL-quorum
branches. It reconciled **410,700 ACK rows across 17 journals**, including failed
controller/checkpoint attempts, without missing/wrong/duplicate/partial results.
Those failed attempts remain failed experiments. The two cases below plus the
final core, pageserver and backup cases cover **10 distinct operational scenarios**.

| Case | Recovery + full verification s | ACK rows | Ambiguous requests | Read errors |
|---|---:|---:|---:|---:|
| Two-of-three safekeeper outage | 42.866 | 5,700 | 3 | 0 |
| S3 gateway outage | 48.707 | 10,100 | 3 | 0 |

The minority acknowledged **0 rows after a 10-second in-flight grace period**.
S3 recovery required native uploaded-LSN convergence, not just a reachable gateway.

### Ingestion, branch and recovered-state queries

Final core fixture: **100 × 10,000-row COPY transactions per backend**, with
indexes, body generation and fsynced witness writes inside the measured load
interval; schema/connection setup excluded. Both acknowledged **1,000,000 rows**,
with zero ingestion errors. These are single-run ingestion results.

| Backend | COPY rows/s | Batch p50 / p99 ms |
|---|---:|---:|
| Neon | 260,100.8 | 29.789 / 155.107 |
| PostgreSQL | 372,839.4 | 18.143 / 89.999 |

Native branch API creation took **85.723 ms**; usable branch SQL, including
creation, deployment/startup and readiness polling, took **8.462 s**. Complete
ancestor-LSN contents and parent isolation passed. A branch is not a backup, and
these timings are not a like-for-like COW speed ratio against logical copying.

Recovered-state query benchmark: one warmup then **20 validated executions per
query/backend**, **80/80 successful requests**. P99 is the maximum of this small
sample. Limits were restored; shared-host contention and warm caches apply.

| Query | Neon p50 / p99 ms | PostgreSQL p50 / p99 ms |
|---|---:|---:|
| customer-orders | 0.102 / 0.352 | 0.102 / 0.125 |
| all-orders | 129.206 / 167.469 | 14.436 / 47.727 |

Raw phase events, native status, original failed runs and prior dated measurements
remain in ignored `results/` archives. All fresh fixtures used the full lifecycle
and owned cleanup; no failed dataset was reset to obtain a pass.

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
- Multi-network kind restart uses hostname DNS to rewrite kubeadm's node IP;
  gateway priority alone cannot guarantee the selected bridge. Setup now preserves
  a kind-network `--node-ip` override in `/etc/default/kubelet`, checks the effective
  process arguments, and leaves the kind entrypoint unchanged. The override is
  scoped to each owned container's current kind address; rerun setup if that address
  allocation changes. This is a lab networking workaround.
- `make test-fencing` exercises six native-exit/error-path controls. PVC replacement
  requires an exited old container, or NotFound plus a healthy CRI listing proving
  absence. Docker/transport errors and unknown states cannot authorize replacement.
- The pinned snapshot/emergency control plane and one pageserver do not reproduce
  managed Neon HA. Storage-controller upcall warnings are expected in this limited
  topology; layer-GC/storage reclamation and managed control-plane behavior are not
  qualified. A branch is not an independent backup. All logical zones share a host.
- The pinned production pageserver rejects its testing-only checkpoint endpoint.
  Cache replacement instead temporarily shortens only `checkpoint_timeout` to 5s,
  preserves ordinary checkpoint distance, waits for native uploaded LSNs and restores
  original tenant overrides. An idle restarted timeline can expose projected LSN 0
  while its previously validated visible remote LSN remains durable; the barrier
  checks these native remote fields, not local disk consistency alone.
- An experimental 16 KiB checkpoint distance caused a proven pageserver
  `OOMKilled` at its 2 GiB limit. That failed attempt preceded cache deletion;
  original settings were restored and all historical ACKs reconciled. An initial
  empty-cache reattach also hit transient Service/API readiness; the corrected
  controller retries the same idempotent tenant/generation attachment only.
- The first logical restore fixture rejected remote SQL in pg_hba.conf. It remains
  a failed report; the rerun allows the exact workload Pod address and passes full
  readback. Backup cleanup restores storage dependencies before compute readiness.
- Unrun: broker outage, peer partitions, permanent worker rescheduling,
  scaling/PDB/drain tests, version upgrades, physical Neon backup/control-plane
  recovery, systematic pressure/soak and production authentication/TLS. Operator reconciliation
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

Kubelet override reference: [kubeadm kubelet integration](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/kubelet-integration/).

## Future work / TODO

Deferred checks for supported configurations; these are **not executed coverage**.
Keep recorded passes, failures and limitations above. Performance work requires
a separately selected depth; this list does not restart paused experiments.

- [ ] Test broker outages and peer partitions separately from the already executed pageserver/S3/WAL-quorum cases.
- [ ] Exercise supported scaling, drain/PDB behavior and permanent worker/local-volume rescheduling with safe fencing.
- [ ] Qualify supported version upgrades, bounded resource/storage pressure and independent physical backup/control-plane recovery.
- [ ] Observe layer GC/long-term storage reclamation and preserve the distinction between the completed logical restore and unrun physical recovery.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
