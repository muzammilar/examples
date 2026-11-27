# SeaweedFS: shared files and filer-loss recovery

Two independent Linux FUSE mounts over SeaweedFS **4.55** with **etcd 3.7.4**.
A native C++17 workload publishes immutable files, checks cross-mount `flock`,
and keeps a writer/reader active during a ten-second loss of both filers. This
tests the filesystem interface separately from the [S3 example](../s3-cluster/).

## Quick start

1. `make docker-up` — build the workload image and start the storage cluster.
2. `make up` — create the two independent FUSE mounts and verify native versions.
3. `make test` — publish 128 files and check both mounts and three native copies.
4. `make down` — verify/export retained ACKs and remove the two application services.
5. `make docker-down` — remove this example's infrastructure, volumes and image tag.

Requires Docker Compose, Python 3.9+, at least 8 Docker CPUs / 8 GiB RAM, and a
Docker-engine `/dev/fuse`. The two application services require `SYS_ADMIN` and
an unrestricted AppArmor mount profile inside their own containers; no privileged
container, host filesystem mount or external credentials are used. The upstream
SeaweedFS entrypoint also needs its user-switch/ownership capabilities. All runtime
services use an internal network without published host ports.

The base cluster’s combined limits are **6.75 CPUs / 5 GiB**, including both
FUSE services. The optional [native HTTP project](projects/native-http/) adds
1 CPU / 512 MiB, for **7.75 CPUs / 5.5 GiB** combined limits. Each volume server
allows 32 × 64-MiB volumes; reserve additional disk for three replicated copies,
metadata and caches before running the paired trials (`COMPARE_FILES=256` by default). The tested Docker VM had 11 CPUs / 26,216,296,448 bytes RAM. Sparse/cache
growth still needs headroom: the bounded corpus allows at most 256 MiB per run.
The observed settled volumes occupied 511,696,896 bytes, excluding images.

`make test` refuses an existing witness. Use `make verify` for retained state;
never delete the fixture or re-seed after a failed case. `FILES=128` and
`PAYLOAD_BYTES=262144` configure the seed, with explicit bounds. A live writer is
limited to 512 files / 180 seconds and stops cooperatively after recovery.

## Topology and commands

| Role | Count | Purpose |
|---|---:|---|
| Masters | 3 | Native Raft topology/volume allocation |
| etcd | 3 | Persistent filer metadata |
| Volume servers | 3 | One per logical zone, replication `200` |
| Filers | 2 | Shared namespace/gateway and distributed lock routing |
| Files writer / reader | 2 | Separate mounts, mount caches and native C++ processes |

| Command | Check |
|---|---|
| `make verify` | Every retained ACK through both mounts and all three volume endpoints |
| `make locks` | Exclusive `flock` denial across mounts with `-dlm=true`; record inode/device identity |
| `make filer-failure` | Prove both filer processes exited 137, hold ten seconds, restore and verify live recovery |
| `make test-production` | Serial verify → locks → filer failure → verify |
| `python3 scripts/observe.py test-production` | Add sampled Docker CPU, memory, disk and network measurements |
| `make resources` | Read only owned-volume sizes and native cgroup lifetime counters |
| `make unit-test` | Seven offline oracle controls, including corrupt-replica, missing-ACK and HTTP fixture-ownership rejection |
| `make api-up` / `make api-test` | Build/start the local HTTP application and verify its baseline |
| `make api-filer-failure` | Live HTTP writer/reader during proven simultaneous filer loss |
| `make api-compare` | Three bounded paired native-HTTP/FUSE trials, no S3 gateway |

`files-workload/` contains the C++ source, CMake/Ninja build, Dockerfile and mount
entrypoint. `config/` holds the etcd-backed filer setup and declared contract;
`scripts/` contains lifecycle, independent payload/replica checks and reporting.
`projects/native-http/` keeps the API workload, tests, comparison and its reports
together; it shares this lab’s explicitly owned backend and cleanup.
`reports/` holds curated evidence. Raw histories, failed trials and resource
samples stay under ignored `results/`; build/research caches stay in `.build/`.
The example uses no sibling Makefile, generated state or machine-local source.
The standard-library Python controllers extend the existing storage-lab approach;
the application and storage-primitive probes are C++.
This direct-chunk audit does not qualify tiny inline-file bodies; add that case
with independent metadata-store verification before claiming small-file coverage.

## Contract

For each 256-KiB deterministic SHAKE256 body: fsynced external intent → exclusive
staging create/write → file fsync/close → rename → directory fsync → fsynced ACK.
The host independently verifies the payload hash. Mutation errors remain ambiguous;
the application never replays them. It continues with new IDs, then reconciles the
original final paths. Once an unacknowledged publication is independently observed,
its retained observation becomes required in later audits.

Live reads revisit the 128 historical files; fresh C++ processes check all newer
ACKs after recovery. Native filer metadata supplies chunk IDs; direct reads from
all three owning volume endpoints check exact lengths/hashes with forwarding
disabled. The workload does not decode SeaweedFS needle files or infer replication
from a row count. All three zones share one host; hardware failure tolerance and
physical power-loss durability are unproven.

`writebackCache=false`, metadata-cache TTL 1 second, `dlm=true`, 16-MiB read cache,
16-MiB write buffer and 256-MiB Go memory target are pinned in the mount entrypoint.
Native 4.55 concurrency defaults remain 128 readers / 128 writers, while each C++
actor issues one operation at a time with 100-ms think time. Native filer retry
behavior is part of the pinned mount implementation. Its metadata-commit budget
does not bound an entire upload or syscall; the outer 180-second supervisor does
not turn a terminated mutation into a known failed write.

## Measured results — 2026-11-27

These are correctness/recovery diagnostics in the approved level-3 batch, with
no competing-backend performance ranking. [Curated metrics](reports/QUALIFICATION.json)
include evidence hashes, resource scopes, old failed trials and cleanup proof.

| Check | Result |
|---|---|
| Initial publication | 128 ACK files / 33,554,432 bytes; 384 direct replica reads / 100,663,296 bytes |
| Final retained histories | **161 ACK files / 42,205,184 bytes**, zero missing/wrong bodies |
| Final physical-copy readback | 483 direct reads / **126,615,552 bytes** across three volume servers |
| Ambiguous final paths | Two confirmed absent through fresh filer reads; none replayed |
| Final ten-second filer outage | 15 new ACKs / one ambiguous create; writer resumed automatically |
| Historical reader | 19 successful full 128-file cycles / one EIO cycle, then automatic recovery |
| Successful operation timing | Writer maximum 20.638 ms; a full 128-file read cycle maximum 1,201.065 ms |
| Failure timing | Ambiguous create returned EAGAIN after 15,038.687 ms; failed read cycle EIO after 13,205.303 ms |
| Longest successful-completion gap | Writer 15.262 s / reader cycle 14.609 s, including deliberate think time |
| Cross-mount lock | Final contender open 1.680 ms; nonblocking flock denied in 0.787 ms while holder remained live |
| Lock identity | Same native inode `1039786563074662847`, different FUSE device IDs 83 / 85 |
| Final production window | 12.703 aggregate CPU core-seconds; sampled charged-memory peak 1,357,991,936 bytes |
| Native volume I/O | Cumulative 127,598,592 written bytes / 397 writes across three volume containers; zero physical-read bytes reported (cache hits) |
| Settled storage | 511,696,896 allocated volume bytes, including replicated data and preallocated etcd WAL; mount-cache apparent bytes 1,319,982 / 1,352,184 |
| Cleanup | Zero owned containers, volumes, networks or local workload-image tags; raw evidence preserved |

The final outage also verified unchanged container/PID/start identities for the
11 unaffected roles. Both filers restarted against retained etcd state. The
19.257-second fault-through-final-verification duration includes the ten-second
hold, health polling and complete readback; it is not a precise election metric.

### Negative outcomes and probe corrections

- Initial startup failed with `su-exec: setgroups: Operation not permitted` because
  the initial container capability set prevented the upstream entrypoint from
  switching users. The required capabilities were restored and startup reran.
- The first `O_RDWR` lock probe waited about 20 seconds in open and acquired only
  after the holder exited. Its failure did **not** demonstrate overlapping lock
  ownership. The corrected probe separates open and flock and uses read-only
  descriptors, avoiding write-open ownership contention. Original logs remain.
- The first outage reader stopped on EIO; the next writer stopped on an ambiguous
  EAGAIN create. Those trials remain failed. All their ACKs survived independent
  readback. The final workload records these transport errors, keeps observing
  recovery, and still fails immediately on wrong bodies, missing ACKs after
  recovery, malformed history or invalid native placement. A passing later trial
  does not erase either earlier failed run or their outage observations.

## Limits and next tests

This qualifies only the stated publication/lock/filer-loss cases. FUSE syscall
success alone does not establish complete POSIX semantics or power-loss safety.
No full general-purpose distributed-filesystem or database-on-FUSE claim is made.
Snapshots/backups and physical host/disk loss are separate boundaries.

Docker metrics cover the final verify/lock/outage window; native I/O counters are
container-lifetime values. Short controls/builders and sub-second peaks may be
missed. etcd's distroless image lacks a shell for the native cgroup probe; Docker
sampling and read-only volume sizing still cover it. Disk-before-load/peak sizing
and device I/O latency remain gaps. Compression benefit/CPU cost is **unavailable**:
the synthetic SHAKE256 corpus had no paired compressed/uncompressed trial. Replica,
WAL, metadata and cache allocation must not be called a compression ratio.

- [ ] One-filer loss with effective connection-routing proof, separately from
  loss of both gateways; asymmetric partitions and metadata-quorum loss.
- [ ] Cross-mount contended updates/rename, open-unlink, cancellation, stale caches,
  lock-owner crash and DLM lease/rejoin behavior. `dlm=false` is unqualified here.
- [ ] Volume loss, permanent empty-device replacement and restored native copies
  through this filesystem interface; existing S3 results do not establish it.
- [ ] Compaction/garbage collection under live traffic, interrupted maintenance,
  capacity pressure, TTL/physical reclamation and a compressibility matrix.
- [ ] Scaling, supported upgrades, independent metadata+data backup/restore and a
  second clean deployment. Retained remount/setup and full teardown ran.
  The interactive `make cli` command is unrun.
- [ ] Matched native-resource CephFS/HDFS shared-file comparisons after equivalent
  correctness gates. Ceph's emulated timings cannot be ranked against this run.
- [ ] Extend the [native HTTP/FUSE comparison](projects/native-http/) to
  S3/gRPC and additional payload/workload shapes.
  Separately compare CephFS kernel versus FUSE/native-library clients for POSIX
  workloads. Measure large sequential I/O and small-file/metadata operations,
  CPU, memory, latency and recovery at matched budgets. Account for filesystem
  versus object semantics and caching; fewer layers do not prove a measured win.
  Match content flush and metadata-publication guarantees; object APIs do not
  automatically supply filesystem rename/lock semantics.
  [Ceph kernel-client guidance](https://docs.ceph.com/en/umbrella/cephfs/mount-using-kernel-driver/).

## Upstream basis

- [Pinned mount options](https://github.com/seaweedfs/seaweedfs/blob/4.55/weed/command/mount.go).
- [Native filer HTTP API](https://github.com/seaweedfs/seaweedfs/wiki/Filer-Server-API)
  (living documentation; recheck against the pinned release for future trials).
- [Flush/fsync implementation](https://github.com/seaweedfs/seaweedfs/blob/4.55/weed/mount/weedfs_file_sync.go).
- [Filer retry/failover path](https://github.com/seaweedfs/seaweedfs/blob/4.55/weed/mount/wfs_filer_client.go).
- [Distributed lock routing](https://github.com/seaweedfs/seaweedfs/blob/4.55/weed/mount/weedfs_file_lock.go)
  and [rename implementation](https://github.com/seaweedfs/seaweedfs/blob/4.55/weed/mount/weedfs_rename.go).
