# StarRocks: operator-managed mutable orders

The official StarRocks operator manages **three FEs and three BEs** in a dedicated
kind cluster. One million mutable orders use three hash buckets and three copies;
100,000 updates and 100,000 stale replays exercise version-aware primary-key
updates. Full-value verification checks the final million-row state and joined
aggregates, native tablet placement, durability settings and physical-copy checksums.

## Quick start

Prerequisites: Docker, Make, Python 3, kind, kubectl and Helm. This local image
loader requires native ARM64 images. Allow 16 GiB Docker RAM and 20 GiB free Docker
disk for imported images and PVCs, plus capacity for existing images.

1. `make kind-up` — build the Go workload, create the private kind cluster and import pinned images.
2. `make up` — install the operator and cluster, wait for native membership and load the orders.
3. `make test` — reconcile complete order values, dimensions, aggregates, tablets and recovery history.
4. `make down` — drop workload tables, uninstall the cluster/operator and delete the owned namespace.
5. `make kind-down` — delete the owned cluster/context, generated configuration and local workload image.

Cluster `starrocks-operator`, context **`kind-starrocks-operator`**, private
`results/kubeconfig`. Every kubectl/Helm call selects this context explicitly.
Kubernetes v1.34.0 has one control plane and three workers; the witness/workload
runs on the control plane, with database members restricted to workers. Preferred
anti-affinity spreads roles; inspect actual placement before interpreting failures.

## Configuration

| Component | Pinned version / per-member limit |
|---|---|
| Operator/chart | 1.11.8; 500m CPU / 512 MiB |
| FE × 3 | StarRocks 4.1.7; 500m CPU / 1 GiB, 640 MiB JVM heap, 2 GiB metadata PVC |
| BE × 3 | StarRocks 4.1.7; 1 CPU / 1.5 GiB, native 1024M memory cap, 2 GiB data PVC |
| Orders workload | Go 1.25.4 build; 2 CPUs / 2 GiB; MySQL driver and standard HTTP/JSON/hash libraries |

FE metadata uses SYNC/SYNC and SIMPLE_MAJORITY; BE tablet metadata sync is enabled.
Tables retain replication three and majority write quorum. These lab limits differ
from the equal-budget Compose comparison; this shape has no ClickHouse comparator.
Use `COUNT=1000000`; `FAULT_BATCH_ROWS=1000 FAULT_PAUSE_MS=1000` bound the live load.
`make benchmark` records 20 validated warm queries per case with p50/p99 timings.

## Implemented operational targets

`make test-production` invokes the following independent cases serially. A live
writer fsyncs attempted/acknowledged/ambiguous IDs outside database PVCs; a separate
reader samples known data. Both remain active throughout native repair. Phase
reports separate read/write successes, failures and latency distributions. Recovery
requires physical replica checks, fresh progress and complete historical readback.

| Target | Intended native behavior |
|---|---|
| `scale-out`, `scale-in` | 3 → 5 BEs, real tablet movement, then decommission and drain 5 → 4 → 3 before lowering the CR replica count. |
| `scale-up`, `scale-down` | Change BE resources through the CR, wait for actual running templates and native convergence. |
| `rolling-restart` | Change a CR template annotation and require replacement UIDs for every member. |
| `failover`, `chaos-fe-leader` | Kill an actual BE process or discovered FE leader through its owned container runtime; verify old-process exit. |
| `chaos-node` | SIGKILL an owned worker, observe NotReady, hold 30 seconds, restart with retained local storage. |
| `chaos-operator` | Stop reconciliation, submit a pending CR update, verify data-plane progress and convergence after operator restoration. |
| `chaos-network` | Apply uniquely tagged, worker-scoped database-port rules; require blocked-packet counters and restore exact rules. |
| `chaos-write-quorum` | Retain three-copy tables while reducing available BEs to one; after 10 seconds require zero new ACKs, then restore copies. |
| `drain-test` | Drain one worker, assert a second unsafe FE eviction is rejected by its PDB, then uncordon and recover local-PVC members. |
| `disk-failure` | Fence the highest BE ordinal, delete its exact owned PVC, restore an empty replacement and require native tablet repair. |

## Measured results

2026-11-15, Apple M4 Pro, native ARM64 Docker, shared 11-CPU / 24.42 GiB VM.
The full lifecycle, complete million-order state/joins, native physical checks,
13 operational cases, final reconciliation and owned cleanup passed. This shape
has different memory limits from Compose and no ClickHouse comparator; these are
bounded observations, not normalized architecture/capacity rankings.

The initial 1,000,000 rows, 100,000 version-2 updates and 100,000 stale replays
are sent in 10,000-row batches. Rates include body generation, upload, server ACK
and witness fsync, excluding schema setup. Every ingestion request succeeded.

| Input phase | Rows/s | Batch p50 / p99 ms |
|---|---:|---:|
| initial | 72,061.4 | 145.902 / 165.702 |
| updates | 65,747.2 | 150.614 / 158.252 |
| stale-replay | 66,427.9 | 145.481 / 158.232 |

One warmup followed by 20 validated timed queries; p99 is the maximum of this
small warm sample. These include application/wire result handling, not engine-only
latency. No independent-host or sustained-capacity claim follows.

| Query | p50 / p99 ms | Errors |
|---|---:|---:|
| customer-current-state | 8.360 / 88.163 | 0 |
| all-current-state | 29.575 / 88.218 | 0 |
| region-join | 75.741 / 156.029 | 0 |

All thirteen passing operational cases are below. Elapsed time includes native
repair and final full data/history verification; it is not an exact election or
first-success timestamp. Node, network, quorum and disk tests include a 30-second
hold, and node time includes Kubernetes heartbeat detection. Raw observation
files retain phase-specific throughput and success/failure latency distributions.

| Case | Elapsed s | ACK rows in case | Ambiguous requests | Read errors | Missing/wrong known reads |
|---|---:|---:|---:|---:|---|
| scale-out | 61.631 | 46,000 | 0 | 0 | 0 / 0 |
| scale-in | 601.158 | 509,000 | 0 | 0 | 0 / 0 |
| scale-up | 83.752 | 11,000 | 52 | 0 | 0 / 0 |
| scale-down | 90.470 | 31,000 | 24 | 0 | 0 / 0 |
| rolling-restart | 94.977 | 24,000 | 49 | 0 | 0 / 0 |
| failover | 12.927 | 7,000 | 0 | 0 | 0 / 0 |
| chaos-fe-leader | 18.122 | 8,000 | 2 | 1 | 0 / 0 |
| chaos-node | 134.581 | 69,000 | 4 | 0 | 0 / 0 |
| chaos-operator | 127.304 | 51,000 | 40 | 49 | 0 / 0 |
| chaos-network | 69.890 | 12,000 | 5 | 4 | 0 / 0 |
| chaos-write-quorum | 100.786 | 31,000 | 37 | 1 | 0 / 0 |
| drain-test | 104.307 | 27,000 | 5 | 0 | 0 / 0 |
| disk-failure | 217.253 | 170,000 | 1 | 113 | 0 / 0 |

Full historical reconciliation covers **1,047,000 acknowledged recovery rows across
14 journals**, with **zero missing ACKs, wrong/duplicate values or partially
committed ambiguous batches**. This includes one failed operator-recovery attempt,
which remains a failed test. There were 265 ambiguous batches: 12,000 unacknowledged
rows were present after recovery, in addition to all acknowledged rows. A failed
response therefore did not prove that its whole transaction failed. No mutations
were replayed to hide that uncertainty. Known baseline reads had zero wrong/missing
bodies, while the explicit read-error counts above expose availability degradation.

Scale-out's saved physical order-table checksums show both BE3 and BE4 owning
workload tablets. The strengthened gate checks order-table placement on all five
BEs; a positive total TabletNum alone can reflect internal statistics tablets.
Decommissioning drained those internal tablets too before either member was dropped.
The quorum case required zero new ACKs after its 10-second in-flight allowance.
The drain case confirmed rejection of a second unsafe FE eviction before uncordon.
Disk replacement required a new owned claim UID, native repair and matching physical
copies; the other five database containers retained their identities.

## Gotchas and limitations

- **Scale-in took 601.158 seconds with traffic live.** Native pending-tablet
  diagnostics reported `consecutive full clone needs to delay`; the effective
  `tablet_sched_consecutive_full_clone_delay_sec` was **180**, matching pinned
  source. Rapid scale-out followed by scale-in repeatedly encounters that guard.
  The test honored it and waited for complete native drain instead of lowering
  replication, bypassing the cooldown or force-dropping nonempty backends.
- Pod anti-affinity and PDB selectors must match **Pod** owner labels
  `orders-fe` / `orders-be`; the StatefulSet object's owner label is `orders`.
  Startup checks actual one-member-per-role placement across three workers.
- A worker killed with SIGKILL becomes Ready=Unknown, not necessarily False.
  The outage test records the actual unavailable condition before its hold.
- One operator-outage run stopped after physical recovery because FE query routing
  still blacklisted a restarted BE (`Alive=true, inBlacklist=true`). The corrected
  gate additionally requires native backend-config queries to succeed while load
  remains live. Blacklists were allowed to clear natively, never manually bypassed.
  Its failed journal and diagnostics remain in the final 14-journal reconciliation.
- The empty-disk case recorded **113 failed reads** with `tablet does not exist`
  on the returning backend. Heartbeats/HTTP readiness preceded usable tablet
  recovery/routing. Replication and zero acknowledged loss did not eliminate those
  temporary client errors. The physical/query-path gate waited for real recovery.
- PDBs constrain voluntary evictions; direct replica-count changes, forced crashes
  and network partitions have different behavior. BE scale-in still needs native
  decommissioning; the operator's StatefulSet scale-down is not a data drain.
- Explicit unrelated-survivor UID/container checks cover the later operator,
  network, quorum, drain and disk cases. Earlier cases retain their after-Pod
  snapshots and native/data checks but predate that added assertion.
- Native XOR32 checksums are not cryptographic file-integrity proofs. Full logical
  body/value verification complements them. Live readers sample immutable baseline
  rows; no fresh-write visibility-lag or linearizability result is claimed.
- Claims request 2 GiB, but BEs see the shared Docker filesystem's capacity; this
  does not qualify quota enforcement or independent block devices. Imported images
  consume that same backing disk. The importer checks capacity and reserves 8 GiB.
- This same-version restart matrix is not a database/operator upgrade or rollback
  test. Independent backup restore, permanent worker loss/local-PV rescheduling,
  bounded EIO/disk-full/OOM, production authentication/TLS and sustained overload/
  soak remain unrun. All logical zones share one physical machine.

Raw results, before/after Pod snapshots, per-phase observations, original failures,
placement and clone-cooldown diagnostics remain in ignored `results/`. See the
[Compose example](../docker-compose-cluster/) for matched ClickHouse workload
semantics and the separately measured eight-case comparison.

## Source and build

`mutable-orders/` is a tracked symlink to the system's Go workload. `prepare.py`
materializes hard links (copies when necessary) into ignored `.build/` before the
local Docker build. Generated JSON and results are ignored. This example owns its
configuration, controller, images, kind cluster and state; no sibling must run first.

Sources: [pinned operator API/source](https://github.com/StarRocks/starrocks-kubernetes-operator/tree/v1.11.8),
[native scale-in procedure](https://github.com/StarRocks/starrocks-kubernetes-operator/blob/v1.11.8/doc/scale_in_shared_nothing_cluster_howto.md),
[StarRocks primary-key tables](https://docs.starrocks.io/docs/table_design/table_types/primary_key_table/),
[pinned clone scheduling defaults](https://github.com/StarRocks/starrocks/blob/4.1.7/fe/fe-core/src/main/java/com/starrocks/common/Config.java).

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
