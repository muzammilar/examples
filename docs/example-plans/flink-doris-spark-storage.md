# Current batch — Flink, Doris, Spark and storage

Implementation requested on 2026-11-26. Work breadth-first through deployment
and correctness before deeper comparisons. The user selected performance
**level 3**, focused practical comparisons. Engine release, connector compatibility and image architecture
must be verified independently. All infrastructure is local and example-owned.

## Deployment sequence

| System | Single-node | Docker cluster | Kubernetes | Representative workload |
|---|---|---|---|---|
| Flink | Local execution in a container | JobManager and distributed TaskManagers | Apache Flink Kubernetes Operator in a private kind cluster | Keyed order state, event-time corrections and checkpoint recovery |
| Doris | One FE and one BE, explicitly RF1 | Three voting FEs and three BEs with verified tablet replicas | Doris Operator, supported stable engine/operator pair | Mutable inventory joins with sequence-controlled upserts and deletes |
| Spark | Containerized local-mode SQL | Standalone master/workers, followed by separate YARN execution | Native Kubernetes submission or supported operator in private kind | Large joins, shuffle, skew and failed-task recomputation |
| Hadoop | Pseudo-distributed HDFS | HA NameNodes, three JournalNodes, ZooKeeper/ZKFC and replicated DataNodes | Private kind StatefulSet fallback unless a maintained operator qualifies | File publication, NameNode failover, replica rebuild and Spark input recovery |

Spark executes on standalone, YARN or Kubernetes cluster managers. HDFS supplies
storage. State the selected combination explicitly and qualify its processes,
metadata and data replicas separately. The initial Compose Spark lab can use
HDFS without requiring a YARN scheduler.

Use SQL for Flink and PySpark for Spark jobs where these are the maintained
interfaces. External producers, readers and independent workload oracles should
use Rust, C++ or Go where supported. Verify engine guarantees through the native
interfaces instead of introducing custom Java test utilities.

## Storage work

- [x] Extend existing Garage and SeaweedFS S3 labs with interrupted multipart completion and fresh full-body reads. Serving-role loss during an incomplete request, retained-part resume, discarded responses and logical abort passed; internal commit interruption and physical reclamation remain unrun.
- [x] Add a separate SeaweedFS filesystem workload with two independent mounts, file publication/rename/readback, cross-mount flock and live filer-outage recovery. The bounded initial contract passed; contended mutation, lock-owner crash, metadata quorum and filesystem disk loss remain follow-ups.
- [x] Add a dedicated native-client [CephFS project](../../ceph/filesystem-ha/). Active-MDS crash, standby promotion, exact ACK manifests and diagnosed retained-setup recovery ran; keep CPU/emulation limits visible.
- [ ] Extend CephFS to whole-guest/OSD loss and independent restore; RGW recovery does not establish the filesystem interface's behavior.
- [x] Qualify HDFS replication and initial live failover in the Hadoop metadata-HA example. Record client-visible publication and retain the failed native JSch fencing case; partitions/quorum loss/independent restore remain follow-ups.
- [x] [JuiceFS dependency recovery](../../juicefs/dependency-recovery/): PostgreSQL retained-process outage and fresh-client S3 outage passed exact ACK/native RF3 checks. Failed/rejected probes and a cached-endpoint observation remain documented. Metadata HA, permanent loss, CSI and independent restore remain unrun.

Garage is an object store. Ceph RGW, CephFS and SeaweedFS S3/mount interfaces need
their own contracts and observed guarantees. Each pipeline includes its own
dependency services. MinIO is excluded from all new work in this batch.

## Pipelines after the deployment examples

- [x] **PostgreSQL → Debezium/Kafka → Flink → Doris.** Process order/customer CDC with out-of-order corrections, cancellations and dimension updates. Crash around a checkpoint and sink commit, then reconcile exact latest rows and totals at a common source watermark. Test the Doris connector's checkpoint/2PC mode separately from its asynchronous batch mode.
- [x] **Kafka → Flink → Iceberg on SeaweedFS → Spark.** [Bounded snapshot/maintenance recovery](../../pipelines/orders-lakehouse/) ran three million acknowledged events, blocked catalog publication, active compaction-worker loss, exact current/pinned reads and orphan-candidate inventory. Eight candidates were preserved; destructive expiry and independent restore remain unrun.
- [x] **HDFS → Spark → Doris.** Publish daily input partitions with manifests, join and aggregate them in Spark, then load Doris under stable batch identifiers. Fail an executor and a DataNode separately. Re-run the same input partition and verify its final rows and receipt count.
- [x] **Live Flink path plus Spark historical correction.** [Historical replay](../../pipelines/orders-cdc-analytics/projects/historical-replay/) passed source-version protection, live cancellation, publisher SIGKILL and original-label commit retry with exact reads from all three fixed replicas. Initial immediate-visibility gate failure and independent in-place recovery remain documented. This replays a corrected source snapshot, not an independently authored correction; hard-delete and dimension-merge races remain future scope.
- [x] **Matched ClickHouse control.** Use the same raw events, dimensions, deletion rules and settled watermark. Compare native serving queries and complete ingestion/processing/serving costs separately. Record preprocessing and materialization costs for every path, including ClickHouse.

## Comparison questions

Measure query latency, time to correct query-visible results, sustainable input
rate, rebuild/backfill time, outage/recovery, acknowledged loss and duplicates.
Include CPU/core-seconds, memory, compression, storage amplification, disk bytes,
IOPS and network across all roles. Match total budgets and durability. Report
native serving tables separately from shared-file scans, and batch versus
streaming freshness at explicit intervals. Establish the winner per workload
after measuring.

## Upstream references

- [Flink deployment and state](https://nightlies.apache.org/flink/flink-docs-stable/docs/concepts/stateful-stream-processing/)
- [Flink Kubernetes Operator](https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-stable/)
- [Doris release selection](https://doris.apache.org/download/)
- [Flink/Doris connector transaction modes](https://doris.apache.org/docs/4.x/connection-integration/data-integration/flink-doris-connector/)
- [Spark cluster architecture](https://spark.apache.org/docs/latest/cluster-overview.html)
- [Spark streaming recovery](https://spark.apache.org/docs/latest/streaming/apis-on-dataframes-and-datasets.html)
- [HDFS HA and fencing](https://hadoop.apache.org/docs/current/hadoop-project-dist/hadoop-hdfs/HDFSHighAvailabilityWithQJM.html)
- [JuiceFS architecture](https://juicefs.com/docs/community/architecture/)

## First-pass execution checkpoint — 2026-11-26

| Example | Verified level-3 evidence | Optional deeper follow-up (not a level-3 blocker) |
|---|---|---|
| Flink Compose | 1M exact output rows; checkpoint restore after worker SIGKILL; failed initial restart policy retained | Independent live source/witness and richer keyed-state workload |
| Flink local | One JVM/container emits 1M exact rows; independent readback and complete lifecycle passed | Publication/process-loss boundaries |
| Flink operator | Clean 1M output, retained repeated setup and combined worker/S3-capacity recovery passed. Undersized store stalled a checkpoint for 237.834 s; initial disk block preserved | Scale/savepoint round trip, manager HA, semantic readiness and external ACK witnesses |
| Spark local | One-JVM million-order join; fresh Spark/Arrow reads; active read-process SIGKILL and unchanged file hashes passed | Interrupted writer publication, bounded storage pressure and independent restore |
| Spark Compose | 1M exact joined rows; active-task worker SIGKILL, native failed-task event and two fresh full-body readbacks | Completed-shuffle owner loss, driver/publication recovery, required deployment shapes |
| Spark operator/HDFS RF3 | 1M exact joined rows after active executor loss; native failed task; two-copy reads, empty PVC replacement and all three physical replica hashes passed | Continuous ACK witnesses, metadata HA/restore, driver/publication loss and final orchestration repeat |
| Doris RF1 | 1M exact rows across retained BE crash, native tablet/core readback | Independent restore and live ACK witnesses |
| Doris RF3 | Three voters/24 current tablet replicas; 1M upserts and three fixed-replica reads. Immediate outage read failed; fresh read recovered at 42.57 s | Live writer/reader witnesses, permanent disk/FE failure; operator baseline now separate |
| Doris operator | Three voters/24 current replicas, 1M revision upserts, retained BE SIGKILL and controller restart passed. Native capacity readiness fixed after preserved setup failure | Live witnesses, FE/node/disk loss, safe scaling, partitions, upgrades and restore |
| HDFS metadata HA | 64 MiB seed, RF3 independent physical hashes; retained-state manual recovery and live OpenSSH fencing/promotion in 29.317 s passed. Native JSch output-drain stall retained | Fresh lifecycle, repeated setup, two passing live trials and owned teardown verified. Next: quorum/network faults and independent restore; comparative performance unrun |
| HDFS RF1 | 64 MiB exact body/block hashes, fsck and retained DataNode crash | Replication and metadata HA |

These are correctness/recovery diagnostics. No level-3 competitive benchmark has
run yet. Curated per-example reports separate missing resource evidence from
executed checks. Storage interfaces and complex pipelines remain unimplemented
in this batch. Existing S3 evaluations are historical evidence, not reruns.

## Storage follow-up — 2026-11-27

Garage 2.6.0 has been pinned and requalified in a fresh nine-node lab: 1,013
API objects, then 863 live ACKs across a 30-second native-owner crash. Final
readback verified 1,876 objects / 76,419,072 bytes and all 5,754 required native
copies. CPU, memory, storage and I/O-byte diagnostics are recorded; I/O counts
and initial-setup sampling remain gaps. Owned cleanup passed. This is not a
new competitive benchmark or proof of multipart commit-boundary recovery.
The [three bounded Garage multipart cases](../../garage/s3-cluster/docs/MULTIPART_RECOVERY_PLAN.md)
subsequently passed: four retained parts resumed after serving-node SIGKILL,
a discarded completion response remained non-ACK despite correct publication,
and abort disappeared logically. Final readback checked 1,395 objects and 4,626
required native copies. An immediate post-delete listing check failed; later
empty readback and explicit cleanup retry passed, with attribution unresolved.
Physical abort reclamation and internal commit interruption remain unrun.
SeaweedFS 4.55 + etcd 3.7.4 subsequently passed the same protocol paths with
its own gateway/controller and native audits: 1,431 objects, 487,206,912 bytes
read directly across three zone copies, and matching current etcd revision 1,534.
All 21 unrelated roles remained unchanged; owned cleanup passed. Native cgroup
I/O counts are recorded for SeaweedFS roles, with the distroless etcd shell gap
explicit.

CephFS 21.2.0 then passed two active-MDS SIGKILL/promotions (24.857 / 28.337 s),
standby restoration and fresh-client reconciliation of 572 acknowledged files /
149,946,368 bytes. All 17 PGs spanned three logical guest hosts. The first repeated
setup exposed an empty-keyring controller defect; in-place credential-file recovery
and the fixed repeat passed without reseeding. A redundant image-removal cleanup
error was preserved, fixed and explicitly retried. CPU/memory/disk bytes, native
whole-device I/O counts and storage/compression gaps are documented in the
[CephFS README](../../ceph/filesystem-ha/). Owned infrastructure was removed;
raw witnesses remain ignored.

The separate [SeaweedFS filesystem cluster](../../seaweedfs/filesystem-cluster/)
then verified two independent FUSE mounts, distributed flock and a ten-second
SIGKILL of both filer processes under live traffic. Final readback checked all
161 ACKed files / 42,205,184 bytes and 483 direct replica reads. Two ambiguous
creates were confirmed absent without replay. EIO reads/EAGAIN creates remained
visible; both actors resumed. Initial capability/probe/error-policy failures stay
documented. CPU, memory, native I/O and allocated storage are recorded, with
compression and measurement gaps explicit. Scoped teardown passed. Next: the
pipeline gates above; filesystem quorum/disk/restore cases and physical multipart
abort reclamation remain further work.

Production-client follow-up (approved level 3):
[CephFS kernel/library](../../ceph/filesystem-ha/projects/kernel-client/) secure
mounts, retained remount and three paired trials passed, **3,128 ACK files per
path**, with resource/storage measurements and preserved Linux 6.8 key-compatibility
failure. Two kernel trials showed five-second reads. Different rename layouts
prevent an isolated client-overhead ranking. Kernel writes during MDS failure
remain unrun. The [native SeaweedFS HTTP/FUSE project](../../seaweedfs/filesystem-cluster/projects/native-http/)
passed native HTTP correctness, live two-filer recovery and three paired
256-file trials. HTTP/FUSE publication medians were **131.062 / 4.323 timed ops/s**,
with differing semantics and resource scopes explicit. The failed 1,000-file FUSE
trial, one unacknowledged-but-published body and recurring one-second stalls remain
preserved. Final readback checked 2,621 ACKs plus that body and all three copies;
scoped cleanup passed.
S3/gRPC and Ceph FUSE comparisons remain further work.
Separate metadata-heavy/small-file and sequential/bulk workloads, cache states,
durability and fault behavior before making performance recommendations.

## Active cross-system and storage scope

Flink and Doris deployment examples are complete for the agreed level-3 scope.
The optional deeper cases above do not reopen that completed work. Current work
implements the five cross-system paths listed above, each in a focused project
with an owned local lifecycle, plus bounded storage recovery follow-ups. Keep
performance at level 3; no exhaustive matrices or long soaks.

Planned paths (bounded storage follow-ups may be interleaved after a completed pipeline):

1. PostgreSQL/Debezium/Kafka/Flink/Doris: exact CDC join and deletion/correction
   semantics, checkpoint-versus-async sink behavior, one live worker failure.
2. HDFS/Spark/Doris: deterministic partition publication, idempotent reload and
   one executor/DataNode failure, with preserved batch receipts.
3. Kafka/Flink/Iceberg/SeaweedFS/Spark: snapshot readback, one interrupted commit
   and bounded maintenance/recovery. Keep its catalog/store owned and local.
4. Live CDC plus historical Spark correction: version-guarded publication and
   exact final watermark reconciliation; then matched ClickHouse control.
5. Storage: bounded CephFS mounted-client MDS/node/disk recovery and fresh restore;
   diagnose the SeaweedFS publication stalls; multipart commit/reclamation cases;
   JuiceFS metadata/object-store outage and recovery. These are focused scenarios,
   not a new exhaustive qualification of every storage topology.

No real AWS configuration, credentials or resources. Run heavy labs serially
within verified total CPU/memory/disk budgets. Each completed project gets its
own curated good/bad results, limitations, scoped cleanup and commit to main.

### Cross-system progress — 2026-11-27

[Order CDC analytics](../../pipelines/orders-cdc-analytics/) completed its million-order
retained recovery, customer corrections, live worker loss, deletions, three fixed-replica
full reads and 420 point-predicate checks. All 244 source transaction ACKs reconciled;
998,000 active joined rows remained per sink. Native memory refusals, terminal job
failure, removed checkpoints and initial RF1 Connect topics remain failed findings,
with in-place/log-based recovery documented. Three short hot-query trials are diagnostics,
not a competitive winner. The final configuration passed a separate 10,000-order
fresh lifecycle, including exact native checkpoint settings and owned teardown.
Next: bounded FUSE stage diagnosis and the remaining pipeline/storage paths above.

The [bounded FUSE publication probe](../../seaweedfs/filesystem-cluster/projects/fuse-publication-stalls/)
subsequently localized the long publications to rename: 28/128 calls exceeded
500 ms; p95 1,019.055 ms. File fsync peaked at 15.334 ms. All 256 ACK files and
768 native copies passed exact readback; resource/compressibility measurements
and scoped cleanup are preserved. DLM retry is a source-informed candidate, not
a proven internal cause. Other storage follow-ups remain open.

The [CephFS kernel-client MDS follow-up](../../ceph/filesystem-ha/projects/kernel-client/)
passed in a fresh lab: native promotion observed at 30.592 s, 512 live ACK files,
157 correct historical cycles, and all 768 library/old-kernel/live-kernel ACKs
retained. Maximum publication latency was 22,253.562 ms despite p99 153.228 ms.
Both kernel namespaces matched exactly; all nine OSDs/17 PGs/three MONs recovered.
CPU, memory, native virtual-disk IOPS/latency, storage and disabled-compression
measurements are preserved. Whole-guest/disk/partition/independent-restore cases
remain open; this MDS result does not substitute for them.

The current Ceph recovery fixture’s full teardown also passed after fresh body
and namespace reconciliation; only curated outcomes are committed.

### Iceberg compatibility selection

The current Iceberg 1.12.0 release publishes Flink 2.3 and Spark 4.1/4.0/3.5
runtime artifacts, not a Spark 4.3 runtime. The HDFS/Spark/Doris path can use
Spark 4.3.0 independently. For the separate Iceberg path, select the newest
maintained compatible Spark 4.1 patch (currently 4.1.4) and verify the native
artifact/source matrix; matching Scala alone does not establish engine compatibility.
Use a local catalog and explicit lab-only S3 endpoints/credentials. No managed
cloud fallback or host credential discovery is permitted.
Sources: [Iceberg release artifacts](https://iceberg.apache.org/releases/),
[Spark releases](https://spark.apache.org/news/).

### HDFS/Spark/Doris execution checkpoint

[HDFS batch analytics](../../pipelines/hdfs-spark-doris/) ran one million source
orders / 10,000 customers into three distinct output batches. Exact Arrow body
and customer-partition checks, three fixed Doris replica reads and stable native
load labels passed. Active-worker SIGKILL produced one failed task/recomputation;
DataNode SIGKILL preserved reads/writes but **failed the 180-second RF3 gate**:
14 blocks remained at RF2. Effective 300-second pending-reconstruction timeout
and 300-second monitor interval correlated with all 14 native timeout messages
at 385.54 s; full RF3 was verified by 430.78 s without intervention. All 225
published physical copies matched. The failed gate remains failed; eventual
recovery is a separate result. Warm query trials are diagnostics, not a winner.
Runtime/startup/parser/preflight findings and resource gaps remain documented.
The full five-command lifecycle and final all-batch reconciliation passed;
owned state was removed. The compatible Iceberg path is now recorded below;
historical corrections/control and bounded remaining storage work follow.

### Iceberg execution checkpoint — 2026-11-27

[Snapshot recovery](../../pipelines/orders-lakehouse/) used native Iceberg 1.12.0,
Flink 2.3.0, compatible Spark 4.1.4 and a PostgreSQL 19.0 JDBC catalog over local
SeaweedFS RF3. Three million source ACKs reconciled exactly, including every field,
Kafka offset and file partition. A proven blocked checkpoint-48 publication held
8,000 records; its TaskManager was killed and the same job restored checkpoint 48.
The separate compaction failure produced one native failed task and recovered
119→9 file replacement while source ACKs continued. Old snapshots remained exact.
Final 198 physical replica reads / 36,668,493 bytes matched; live Parquet column
compression was 7.014×. Eight unreferenced candidates / 503,220 bytes were exported
with host-verified hashes, alongside all source witnesses and Spark events.
Retained setup, final drain and scoped teardown passed. Initial setup/adapter
failures and CPU/I/O measurement gaps remain explicit. This is level-3 recovery
evidence, not sustained capacity or an independent-host durability claim.

Next: interleave a bounded storage recovery follow-up, then version-guarded live
CDC plus Spark historical correction and the matched ClickHouse control. Do not
reopen completed Flink/Doris deployment examples for optional deeper matrices.

### JuiceFS storage follow-up

[JuiceFS dependency recovery](../../juicefs/dependency-recovery/) completed bounded
metadata and object-gateway outages in separate retained fixtures. The qualified
S3 case retained 639 ACK files / 167,510,016 bytes, with one explicit ambiguous
fsync and zero missing/corrupt ACK bodies across 1,917 native copies. Metadata
recovery retained 640 ACKs. Historical/client-cold reads, latency maxima, CPU,
memory, block bytes, storage and disabled compression are recorded. Failed Docker
identity and warm-cache probes remain failed/rejected; a confounded IP-movement
case exposed old-endpoint retries consistent with the native one-minute DNS cache.
Both fixtures are verified and removed. Ceph whole-guest/disk/restore work still
requires the existing 25-GiB free-Docker-space gate; do not weaken it to fit the VM.
Next cross-system work is live CDC plus Spark historical correction, then the
matched ClickHouse control; completed Flink/Doris deployment examples stay closed.

### Historical replay checkpoint

[Historical replay beside live CDC](../../pipelines/orders-cdc-analytics/projects/historical-replay/)
completed the bounded follow-up: Spark independently matched 199,672 snapshot rows;
10,000 source rows then advanced, including 572 cancellations. Source ACKs advanced
128 → 129 during publisher SIGKILL. Both original-label transactions became VISIBLE
and repeated commit reconciled native finished-label errors. All 999,428 current
active rows matched on each fixed replica, with all 330 source ACKs reconciled.
The first controller's immediate-visibility failure remains failed; it was recovered
in place before the separate corrected case. CPU/memory, block bytes, settled disk
and compression limits are reported; owned teardown passed. Next is the matched
ClickHouse control, with no throughput winner claimed from these diagnostics.


### Matched native ClickHouse control — completed bounded level 3

The [control](../../pipelines/orders-cdc-analytics/projects/clickhouse-control/)
consumed the same 1,012,100-event fence as one checkpointed Flink/Doris sink.
All 998,000 active eight-field rows matched on three copies of each backend.
Serving caps were 4.5 CPU/12 GiB each, including coordination. ClickHouse
retained-replica loss passed 2,048 new quorum-ACK rows and 275 live reads.
Failed Keeper sizing, SQL alias, controller and retained-start ordering phases
remain documented with in-place recovery. All owned infrastructure was removed.
Doris short query timings varied after restart (observed FE CPU throttling);
there is no stable overall performance winner or equal end-to-end ingestion-cost
claim. Optional deeper comparisons are future work, not level-3 blockers.
Next: bounded CephFS whole-guest/disk loss and independent restore, retaining the
25-GiB free-space startup guard. Completed Flink/Doris deployment labs stay closed.
