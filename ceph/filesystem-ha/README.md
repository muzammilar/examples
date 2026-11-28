# CephFS: native clients and storage recovery

Ceph Umbrella **21.2.0**, three Linux guests and nine BlueStore OSDs. One active
MDS and one standby run on different guests; a C++17 `libcephfs` writer and an
independent reader stay connected while the active MDS is killed. Fresh clients
then reconcile every acknowledged file against an external witness and an
independent payload oracle. This is a filesystem-interface lab, separate from
the [older RGW/S3 project](../s3-cluster/).

## Quick start

1. `make docker-up` — build the pinned guest image and boot three owned guests.
2. `make up` — provision CephFS and build/install the native C++ application.
3. `make test` — seed 128 files and verify complete bodies and read-only access.
4. `make down` — export/reconcile all ACKs, then remove CephFS and its two pools.
5. `make docker-down` — destroy only this example's Docker infrastructure/disks.

Requires ARM64 Docker, Compose, Python **3.10+**, 11 Docker CPUs, 22 GiB RAM
and 25 GiB free Docker disk **after image building**. Runtime refuses growth
below 10 GiB free. The measured engine had 11 CPUs / 26,216,296,448 bytes RAM;
the guests container has an 8-CPU / 16-GiB cap, each guest 3 vCPUs / 5 GiB.
The control service has 1 CPU / 512 MiB. TCG emulation oversubscribes guest CPU
relative to the outer cap. Run heavy labs serially. No host compiler is needed.
If the default `python3` is older, select an installed supported interpreter with
`make PYTHON=python3.13 <target>` (or put it first in `PATH`).

`make test` deliberately refuses an existing fixture. Use `make verify` for
retained state; do not delete its witness to force a new seed. Repeated `make up`
and `make verify` are separate invocations. Finish the full teardown before a new
fixture. Cleanup refuses when acknowledged bodies cannot be reconciled.

## Commands and files

The bounded guest/disk/file-restore follow-up passed; see its
[design and commands](docs/STORAGE_RECOVERY.md). Existing measured MDS and
kernel-client results below remain separate.

| Command | Purpose |
|---|---|
| `make verify` | Export every retained journal; fresh-client exact-body reconciliation and native topology checks |
| `make failover` | Discover active MDS, runtime-mask it, prove SIGKILL, await a different active MDS and restore standby |
| `make test-production` | Serial verify → MDS failover → guest loss → disk replacement → optional kernel case → verify; fresh baseline required |
| `make guest-failure` / `make disk-failure` | Hold an owned guest or disk down with live clients, then reconcile ACKs and native redundancy |
| `make backup` / `make restore BACKUP_MANIFEST=...` | Actual-byte file backup and fresh-FSID restore; see the [storage runbook](docs/STORAGE_RECOVERY.md) |
| `make resources` | Save guest daemon/cgroup counters, Ceph storage statistics and sparse-disk usage |
| `python3 scripts/observe.py test-production` | Also sample owned Docker CPU/memory/disk/network during the serial test |
| `make recover-keyrings` | Narrow recovery for the diagnosed empty-keyring bootstrap bug, followed by old-ACK reconciliation |
| `make unit-test` | Offline corrupted/truncated/misordered witness and fixture-ownership controls |
| `make kernel-up` / `make kernel-test` | Prepare secure guest kernel mounts; verify matched bodies |
| `make kernel-reopen` | Unmount/remount and reread retained ACKs without reseeding |
| `make kernel-mds-failure` | Live kernel writer/reader during a proven active-MDS crash; separate from the library result |
| `make kernel-compare` | Three paired, 1,000-file access-path diagnostic trials |

- `files-workload/`: C++ application, CMake/Ninja and Docker build. Native
  `libcephfs`, OpenSSL and nlohmann/json handle storage, hashing and JSON.
- `native/`: allowlisted guest provisioning, workload actors, fault and recovery.
- `scripts/`: host lifecycle, independent Python payload oracle, resource collector
  and report generation. This extends the existing standard-library Ceph/QEMU
  controller rather than introducing a second controller implementation.
- `docker/`, `docker-compose.yml`: owned infrastructure; no host ports, real
  credentials, Docker socket mounts, privileged containers or host block devices.
- `vm-runtime/`: symlinks to tracked boot/capacity source. `prepare_build.py`
  copies them as regular files into ignored `.build/runtime/` before building.
  No sibling Makefile or generated state is used.
- `projects/kernel-client/`: kernel mounts, local Docker build, comparison, recovery
  and results; the parent owns its guest lifecycle.
- `reports/`: compact curated measurements; ignored `results/` contains immutable
  original journals, exports, actor logs, FSMap observations and raw samples.

The workload is managed through the Compose control service but executes inside
the guests, where it can reach the private Ceph network. It uses the native C API;
the [kernel-client project](projects/kernel-client/) adds guest-only kernel mounts
and a bounded library-versus-kernel comparison. FUSE remains untested. There is no
separately managed host client.

## Contract and topology

Three MONs, two MGRs, nine OSDs, one active MDS and one standby. Both filesystem
pools use **size=3, min_size=2**, CRUSH host placement and disabled compression.
Clients verify effective settings and recovered PG placement. All three guests
share one Docker VM and physical host: these are logical failure domains.

Each 256-KiB immutable file is deterministic SHAKE256 data. Publication writes an
exclusive staging file, calls `ceph_fsync`, closes it, renames it into its final
directory and calls `ceph_sync_fs` **before** appending/fsyncing an ACK. No ambiguous
mutation is retried. The witness resides on the guest OS disk outside the Ceph
pools and is exported to the host before injection and after recovery. It is not
an independent-host/power-loss witness. The fresh verifier reads exact bytes and
EOF; the host independently checks payload/hash identity. This tests publication
survival after one MDS process loss, not general filesystem linearizability.

Live historical reads cover the seed; new writer ACKs are reconciled afterwards.
`FILES=128 PAYLOAD_BYTES=262144` changes baseline size, capped at 256 MiB.
Live actors use one outstanding request, a 100-ms think time and a 240-second
ceiling. Native calls can stall; the controller bounds waits and preserves state
on failure. RPC errors are availability observations; wrong successful bodies or
missing ACKs fail correctness. No capacity or sustained latency winner is claimed.

## Results — 2026-11-27

The initial run published **128 files / 33,554,432 bytes**. The first MDS crash
promoted `fs1` after `fs0` SIGKILL in **24.857 seconds**, restored `fs0` as standby
and verified **348 ACKs / 91,226,112 bytes** through fresh native clients.
During that fault, the writer recorded **220 ACKs / 0 ambiguous outcomes** and the
reader **701 exact reads / 0 RPC errors or wrong bodies**. Requests stalled:
maximum publication duration **18,845.017 ms**, maximum historical read duration
**5,782.964 ms**. These are emulated recovery diagnostics, not uncontended capacity
benchmarks. Read-only CephX rejected a create as expected. Six offline witness
negative controls passed.

| First crash diagnostic | Measured scope/value |
|---|---|
| Docker CPU | 281.680 core-seconds across sampled owned containers, including QEMU |
| Guests CPU utilization | Interval-average 98.60–635.53% of one core; 8-core outer limit |
| Docker charged memory | Sampled aggregate peak 11,580,837,888 bytes |
| Docker disk read / write | 181,915,648 / 283,426,816 bytes in the sampled window |
| Native writer | 6.655 guest core-seconds; 39,931,904-byte systemd memory peak |
| Native reader | 8.375 guest core-seconds; 45,629,440-byte systemd memory peak |
| Settled file-data pool | 91,226,112 logical bytes; 273,678,336 replicated data bytes |
| Metadata pool / whole Ceph raw use | 10,844,467 / 648,855,552 bytes at the later identified snapshot |
| All owned QCOW2 allocation | 8,722,812,928 bytes, including guest operating systems |
| Compression | Codec `none`; both pool compression-byte counters zero; measured codec ratio unavailable |

See [first-trial evidence summary](reports/FAILOVER-1795792788550016000.json).
Docker I/O operation counts and native daemon `io.stat` were unavailable (daemon
IOAccounting was off). Guest whole-SCSI-device counters nevertheless recorded
**19,797 reads / 53,068 writes**, **780,613,632 / 4,407,309,824 bytes** cumulatively
since boot, including provisioning. Device-mapper duplicates and OS root disks
are excluded. These storage observations include replication/metadata overhead and
must not be presented as compressed/uncompressed equivalents.

### Failed repeat and in-place recovery

The first retained `make up` returned success, but the following clients failed
to mount with `Operation not permitted`. Both local client keyring files had
become **zero bytes**. A no-op `ceph fs authorize` returns success without a new
keyring; the initial controller incorrectly treated its output as one.
This is a **lab bootstrap defect**, not evidence of lost Ceph data. No second
fault was injected and no publication started. Original failed logs are retained.

The recovery fetched the existing CephX identities with `ceph auth get`, restored
six keyring copies and reverified all 348 acknowledged files without resetting
pools or reseeding. Setup now authorizes and fetches the keyring separately,
validates it before replacement and provides `make recover-keyrings` for this
diagnosed case. Subsequent retained-state/fault qualification is recorded in the
curated reports; failed evidence is never relabeled.

The corrected repeat setup and serial `test-production` then passed. A reverse
`fs1` → `fs0` failover took **28.337 seconds** with **224 new ACKs / 0 ambiguous
publications**, **842 exact historical reads / 0 RPC errors**, and **572 total
ACKs / 149,946,368 bytes** reverified afterwards. Maximum publication/read durations
were **20,214.553 / 7,029.910 ms**; completion gaps were **20.327 / 7.134 s**,
including the 100-ms deliberate think time. All **17 PGs** had three replicas on
three distinct guest hosts, all nine OSDs were up/in and all three MONs were in
quorum. Promotion times include Compose/SSH polling overhead (roughly 4–5 seconds
per observation), rather than a precise election timestamp.

The full final production-target observation consumed **476.179 Docker
core-seconds**, peaked at **11,844,878,336 charged memory bytes**, and sampled
**539,514,368 read / 368,824,832 write bytes**. This window includes pre/post
verification, so it cannot be compared directly with the first fault-only window.
See [second-trial metrics](reports/FAILOVER-1795793602008061000.json) for phase
latencies, resource scope and storage counters.

`down` reverified/exported all ACKs before removing the filesystem/pools.
The initial `docker-down` removed its infrastructure but returned an error when
the wrapper tried to remove an image Compose had already removed. The wrapper
now checks absence; an explicit retry passed. The failed cleanup log remains.
Final checks found **zero owned containers, state volumes or image tags**; journals
and failed evidence remain under ignored `results/`. An interactive `make cli`
session and a second fresh deployment have not been run.

## Guest and disk recovery — 2026-11-27

A fresh fixture seeded **128 × 64-KiB files (8 MiB)**. A persistent C++ writer
and historical reader on guests 1/2 stayed live through faults on guest 0.
The final audit preserved **1,788 ACK files / 117,178,368 bytes**, zero ambiguous
or missing/wrong bodies. Both cases restored nine OSDs, all 17 active+clean PGs
across three guest hosts and full MON/MDS redundancy.

| Observation | Whole guest power-off | Populated-disk detach/replacement |
|---|---:|---:|
| New live file ACKs | 1,032 | 628 |
| Historical read successes | 1,642 | 1,099 |
| New ACKs / reads observed while held down | 6 / 9 | 14 / 30 |
| Native recovery after release/start of repair | 150.966 s | 75.528 s |
| Maximum write operation | 84,286.542 ms | 4,375.622 ms |
| Maximum read operation | 94,037.202 ms | 67.728 ms |
| Largest writer completion gap | 84,395 ms | 4,485 ms |
| Largest reader completion gap | 94,143 ms | 151 ms |

Zero RPC errors did **not** mean uninterrupted availability: native calls stalled
for up to 84/94 seconds during the mixed-role guest outage. This case does not
isolate which lost role dominated the delay. Recovery briefly had all copies
back but a `MON_CLOCK_SKEW` health warning. It cleared automatically; the later
check reported NTP synchronized, with no threshold or clock change by the harness.
Clock drift versus emulation/scheduling effects was not isolated.

The lost disk owned **four nonempty PGs**. Native `safe-to-destroy` was required;
OSD 0 retained its numeric ID with a new UUID on an empty, serial-allowlisted
spare. The old disk remained fenced. A later reboot of that replacement mapping
is unrun. Phase `degraded` spans the held outage and subsequent recovery; its
entire operation count must not be described as traffic during the held outage.
The smaller held-down counts above were captured before recovery began.

| Whole observed workflow | Guest case | Disk case |
|---|---:|---:|
| Docker CPU, core-seconds | 920.096 | 682.444 |
| VM average CPU, percent of one core | 225.485% | 238.271% |
| Sampled aggregate charged-memory peak | 11,347,034,112 B | 10,447,011,840 B |
| Docker disk read bytes | 571,289,600 | 294,369,792 |
| Docker disk write bytes | 2,107,792,384 | 1,089,797,120 |
| QCOW2 allocated bytes at final snapshot | 8,907,522,048 | 9,423,892,480 |

These windows include prechecks, emulated recovery and fresh verification, not
fault-only engine costs. Native SCSI read/write operation counts and bytes,
actor CPU/peak memory, CPU utilization ranges, pool compression counters and
allocation remain in the reports. At the final source snapshot, file data stored
117,178,368 bytes and used **351,535,104 replicated bytes (3×)**. Compression was
disabled and its counters were zero; replication is not a codec ratio. QEMU CPU
and guest-native CPU are different scopes and cannot be added.

- [Guest report, including transient health evidence](reports/STORAGE-process-failure-1795840433906362000.json).
- [Disk replacement report](reports/STORAGE-disk-failure-1795840888169205000.json).
- [Commands, fencing and file-level restore scope](docs/STORAGE_RECOVERY.md).

The complete ACK cut was exported as **119,214,080 uncompressed archive bytes**
and independently validated before source destruction. The original containers
and state volume were then verified absent. A fresh FSID restored **all 1,788
files / 117,178,368 bytes**, zero missing/wrong bodies or ambiguous outcomes,
using archived bytes rather than generation. Independent clients verified the
saved ACK cut and three-copy placement. Both clusters' teardown passed; the final
owned image was removed too.

| File-backup workflow diagnostic | Backup | Fresh restore |
|---|---:|---:|
| Controller elapsed, including checks/resources | 157.417 s | 187.368 s |
| Docker CPU, core-seconds | 299.980 | 507.426 |
| VM average CPU, percent of one core | 190.124% | 271.369% |
| Sampled aggregate charged-memory peak | 10,687,287,296 B | 11,972,923,392 B |
| Docker disk read / write bytes | 234,401,280 / 411,206,144 | 124,513,280 / 922,734,592 |
| Guest whole-device read / write I/Os | 3,576 / 11,665 | 1,905 / 44,048 |
| QCOW2 allocated bytes | 9,512,038,400 | 8,877,289,472 |

The archived files allocate 134,955,008 host file-block bytes; this is separate
from their 119,214,080-byte lengths and backend replication. Guest I/O includes
whole OS and data/spare disks, excluding partitions/device-mapper duplicates.
Fresh guest provisioning is excluded from the restore duration; emulated workflow
timings are not production RTO estimates. The archive remains on the same computer,
not an off-site physical backup. ACLs/xattrs, hard links, online snapshots and
full metadata/configuration restoration were not tested.

**Configuration finding:** the fresh metadata pool read back `pg_num=32` and
`pgp_num=8`, although bootstrap requested eight PGs; autoscaling was off at
readback. Consequently the destination had **41 healthy PGs**, versus 17 in the
source. Creation-time autoscaling is a suspected race, not an isolated cause.
All copies and bodies passed; no equal-PG-layout performance comparison is claimed.
Fix creation ordering and assert effective PG counts in a future targeted run.

[File backup/restore report and cleanup receipts](reports/FILE-RESTORE-bc19798d5a88422796e56e7b14c94638.json).
Regenerate it after both teardowns with:

```sh
python3 scripts/backup_report.py \
  results/backups/bc19798d5a88422796e56e7b14c94638/manifest.json \
  results/backup-create-1795841187379934000 \
  results/backup-restore-1795842366688340000
```

## Deployment limits and next tests

This manually provisioned systemd lab exercises native daemons; it does not qualify
cephadm or Rook reconciliation. The 512-MiB MDS cache and 1-GiB OSD memory target
are constrained lab deviations, not production sizing advice. Separate real
hosts/disks and adequate resources are required to establish hardware tolerance.
QEMU needs `TCMALLOC_STACKTRACE_METHOD=generic_fp` and `pauth=off`; those emulator
workarounds are not production recommendations.

CPU, charged memory and Docker disk/network counters are sampled during setup
and the fault; native workload-unit CPU/peak memory and I/O, guest whole-device
I/O counts, pool bytes and QCOW2
allocation are captured separately. Docker CPU includes emulation; guest CPU
cannot be added to it. Compression counters are reported with codec `none`; no
compressed counterpart or compression CPU cost was measured. Fine-grained device
latency, physical power-loss durability and all build/short-job resource peaks
remain measurement gaps. Storage amplification includes replicas and metadata;
it is not a compression ratio.

- [ ] Native deep scrubs and independent raw-replica checks; an active+clean PG
  alone is not a full checksum scrub.
- [x] Native-library whole-guest and populated-disk loss, fenced empty-spare
  replacement and independent fresh-cluster file-level restore.
- [ ] Fix and verify creation-time metadata PG-count drift; repeat a retained
  guest boot after disk replacement to exercise the persisted spare mapping.
- [ ] MON quorum loss, peer/client partitions and sustained MDS outage beyond the
  reconnect window, including fencing and ambiguous rename outcomes.
- [ ] Kernel/FUSE mount primitives, close-to-open visibility, contended rename,
  deletes and client-process loss; live readers of newly acknowledged files.
- [ ] MDS scaling, upgrades, metadata pressure, online/full-metadata backup and
  interrupted restore. Current restore covers the quiescent immutable ACK set.
- [ ] Matched native-machine comparisons with HDFS/SeaweedFS for shared-file
  workloads, after equivalent correctness/recovery gates pass.

## Upstream basis

- [Umbrella 21.2.0 release](https://docs.ceph.com/en/latest/releases/umbrella/).
- [Manual MDS deployment and sizing](https://docs.ceph.com/en/umbrella/cephfs/add-remove-mds/).
- [Native C API](https://github.com/ceph/ceph/blob/v21.2.0/src/include/cephfs/libcephfs.h)
  and [client flush implementation](https://github.com/ceph/ceph/blob/v21.2.0/src/client/Client.cc)
  (`Client::_sync_fs`: data, caps/metadata and unsafe-request completion).
- [Authorization implementation](https://github.com/ceph/ceph/blob/v21.2.0/src/mon/AuthMonitor.cc)
  (`CAPS_UPDATE_NOT_REQD` returns before encoding a keyring).
