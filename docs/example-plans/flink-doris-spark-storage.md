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
- [ ] Investigate JuiceFS Community over a lab-owned supported S3 backend and an explicitly durable metadata service. Test mount ownership, metadata loss and object-store outage separately. Add it only after the current four-system baseline is runnable.

Garage is an object store. Ceph RGW, CephFS and SeaweedFS S3/mount interfaces need
their own contracts and observed guarantees. Each pipeline includes its own
dependency services. MinIO is excluded from all new work in this batch.

## Pipelines after the deployment examples

- [ ] **PostgreSQL → Debezium/Kafka → Flink → Doris.** Process order/customer CDC with out-of-order corrections, cancellations and dimension updates. Crash around a checkpoint and sink commit, then reconcile exact latest rows and totals at a common source watermark. Test the Doris connector's checkpoint/2PC mode separately from its asynchronous batch mode.
- [ ] **Kafka → Flink → Iceberg on SeaweedFS → Spark.** Flink publishes table commits while Spark reads pinned snapshots and performs a bounded backfill or compaction. Interrupt publication and maintenance separately. Check snapshot ancestry, orphan files and every acknowledged input ID before cleanup.
- [ ] **HDFS → Spark → Doris.** Publish daily input partitions with manifests, join and aggregate them in Spark, then load Doris under stable batch identifiers. Fail an executor and a DataNode separately. Re-run the same input partition and verify its final rows and receipt count.
- [ ] **Live Flink path plus Spark historical correction.** Feed recent orders through the CDC path and rebuild an older interval with Spark. Publish explicit source versions so a late live update survives backfill. Compare exact results after interrupted publication and retry.
- [ ] **Matched ClickHouse control.** Use the same raw events, dimensions, deletion rules and settled watermark. Compare native serving queries and complete ingestion/processing/serving costs separately. Record preprocessing and materialization costs for every path, including ClickHouse.

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

| Example | Verified evidence | Next gate |
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

Future production-client work (explicitly deferred, not today's execution):
compare native SeaweedFS S3/filer APIs with FUSE where applications can use object
semantics, and CephFS kernel/FUSE/library clients where POSIX access is needed.
Separate metadata-heavy/small-file and sequential/bulk workloads, cache states,
durability and fault behavior before making performance recommendations.
