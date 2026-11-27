# CephFS kernel client

This project belongs to the parent `filesystem-ha` lab. It shares that lab's
three owned Linux guests, nine OSDs, filesystem, lifecycle and cleanup. It is not
a second independently running cluster. All mounts are inside the QEMU guests
managed by Docker Compose, never on the user's host. No S3 gateway, AWS SDK,
account, credentials or remote storage is involved.

## Quick start

Run from `ceph/filesystem-ha/`:

1. `make docker-up` — prepare the owned guest infrastructure.
2. `make up KERNEL_CLIENT=1` — provision Ceph and prepare the kernel clients.
3. `make test KERNEL_CLIENT=1` — verify the library and kernel-client corpora.
4. `make down` — reconcile witnesses and unmount clients before deleting CephFS.
5. `make docker-down` — remove only the owned guest infrastructure and disks.

Against an already initialized parent lab, use `make kernel-up`, then
`make kernel-test`. Repeated verification uses `make kernel-verify`; never reseed
an existing journal. The parent README lists hardware/disk requirements.

## Files and contract

`control.py` orchestrates the parent-owned control service. `native.py` handles
only allowlisted guest mounts, retained kernel upgrades, binary/witness copies
and client execution. The tracked `posix-workload-source` symlink exposes the
repository's POSIX C++ workload. `prepare.py` materializes its source as hard links (copies if unsupported) into
ignored `.build/kernel-workload/`; the local Dockerfile builds a regular artifact.
Edit the tracked source, never its materialized build context.
No sibling Makefile or sibling generated files are used.

The reader guest mounts read-only with its own read-only CephX account. Both
clients require the kernel `ceph` filesystem, the exact lab FSID and encrypted
Messenger v2 (`ms_mode=secure`). Native SDK controls also explicitly require secure
client transport. The kernel build checks Ceph's filesystem magic, uses the same
`42193:<run>:<sequence>` SHAKE256 corpus as `libcephfs`, and adds `syncfs` after
file fsync/close/rename/directory fsync. ACKs are fsynced outside the Ceph pools
and exported before independent readback. No timeout is treated as proof that
a mutation did not commit.

## Compatibility finding and current status

The first mount with the bundled Ubuntu **6.8.0-147** kernel failed before writes:
`adding ceph secret key to kernel failed: Invalid argument`. The owned client key
was type 2 (`aes256k`). Ceph 21.2's secure default needs a Linux **7.0+** kernel
client. The failed mount log is retained; no authentication downgrade is used.

The project installs pinned Ubuntu-supported packages
`linux-image-7.0.0-44-generic=7.0.0-44.44~24.04.1` and matching modules, then
reboots the two owned client guests serially. It waits for a changed boot ID,
the expected kernel and all nine OSDs/PGs recovered before proceeding. This is
lab prerequisite preparation, not a live-workload rolling-upgrade qualification.
The broad HWE meta-package download was stopped before installation because it
pulled unrelated physical GPU/NIC firmware; its log remains preserved.

Both native-library and kernel baselines passed: **128 ACK files / 33,554,432
bytes each**. Kernel mounts on two independent guests used `ms_mode=secure`;
the reader was read-only. Unmount/remount recovery preserved exact bodies without
reloading data. The three paired trials and final reconciliation passed:
**3,128 ACK files / 819,986,432 bytes per path**, zero wrong or missing ACKs.
All nine OSDs were up/in, all 17 PGs active+clean on three logical guest hosts,
and all three MONs in quorum.

## Measured results — 2026-11-27

[Curated measurements and provenance](reports/COMPARISON-2026-11-27.json).
Three trials per path, one worker, 1,000 files × 256 KiB per trial; order
library/kernel, kernel/library, library/kernel. These are **timed-operation rates**,
excluding payload generation, external witness fsync, connection setup and
orchestration. The reference corpus is recent-write/read-after-write, with no
explicit cache eviction; physical cache residency is unverified.

| Access path | Publish ops/s, median (min–max) | Read ops/s, median (min–max) | Publish p50 / p99 across trials, ms | Read p50 / p99 across trials, ms |
|---|---:|---:|---:|---:|
| `libcephfs` | 18.155 (18.149–18.172) | 60.651 (59.071–61.295) | 53.55–53.92 / 73.84–79.04 | 15.79–16.50 / 23.97–24.60 |
| Kernel | 16.132 (15.827–16.360) | 28.509 (26.171–41.883) | 60.06–62.31 / 78.19–80.28 | 15.62–27.27 / 35.84–43.82 |

Two kernel trials had maximum read durations **5,011.450 / 5,022.398 ms**;
the third maximum was 44.801 ms. No fault was injected during these trials.
The stalls remain observed, with attribution unresolved; no data mismatches
accompanied them. The small sample cannot qualify production p99/p99.9 behavior.

This is a comparison of the implemented paths, **not isolated client overhead**:
the SDK renames across `/staging/<run>` and `/objects/<run>`, while the kernel
workload renames within `/<run>`. Metadata work differs. Both use secure transport,
RF3/min-size2, file fsync/close/rename and whole-client/filesystem sync before ACK;
the kernel also directory-fsyncs. These differences prevent a clean speed ranking
of the two client mechanisms.

| Resource scope | Observed value |
|---|---|
| QEMU guests CPU during complete comparison/verification | 2,328.476 core-seconds; mean 266.81%, sampled intervals 72.92–613.46% of one core |
| Sampled entire owned stack memory peak | 13,793,054,720 bytes (includes guest RAM/caches and control jobs) |
| SDK process peak RSS | Writer 255,332–255,716 KiB; reader 253,416–256,260 KiB |
| Kernel process peak RSS | Writer 7,112–7,172 KiB; reader 9,108–9,140 KiB; excludes kernel client memory |
| Process CPU, user+system | SDK writes 25.403–25.816 s, reads 13.665–14.400 s; kernel writes 7.355–7.602 s, reads 6.307–6.606 s |
| Guest whole OSD-device I/O, complete comparison window | 3,179,646,976 read bytes / 24,401 reads; 5,561,917,440 write bytes / 165,294 writes |
| Docker guests block bytes, sampled comparison | 3,184,778,240 read / 7,215,529,472 write; includes OS/witness work |
| Logical data / replicated data-pool allocation | 1,639,972,864 / 4,919,918,592 bytes (3×, not compression) |
| Metadata pool / all raw Ceph used | 190,008,038 / 6,233,296,896 bytes |
| Sparse owned guest disks allocated | 13,331,996,672 bytes, including guest operating systems |
| Compression | Disabled; compressed/under-compression counters zero. No codec comparison ran. |

The kernel's small application RSS omits kernel memory; SDK threads/buffers live
in process RSS. Whole-stack resource measurements include all paired work and
verification and cannot be assigned to a single path. Virtual-device I/O is not
physical device latency or power-loss evidence.

## Limits and next tests

Kernel-client remount recovery passed. Kernel writes during MDS crash, whole-guest
loss, disk loss, partitions, independent restore, physical cold reads and a
matched rename-layout comparison remain **unrun**. The parent’s prior SDK MDS
failover result does not qualify the kernel writer for those failures. Kernel
package upgrades were preparation; no live-write rolling-kernel-upgrade claim.
FUSE is also unrun in this CephFS lab.

Both lifecycle teardown steps passed after full reconciliation: guest kernel
mounts were removed, CephFS and its pools deleted, owned Docker containers/disks
and build tag removed. Original witnesses/logs remain ignored locally.

## Approved comparison scope

The user selected **level 3**, a focused practical comparison. After kernel
correctness/recovery gates, use three fresh-process paired trials with a bounded
1,000-file / 256-KiB corpus (250 MiB per path per trial), alternating order. Match
payload bytes, RF3/min-size2, encrypted transport and publication barriers.
Record per-operation ms, throughput, CPU, memory, disk bytes/I/O and storage;
report compression comparison as unmeasured unless a paired codec trial runs.
Record SDK/guest-kernel/emulation versions and actual resource limits.

Distinguish fresh-client from physical cold-cache reads. Do not drop caches on
the shared Docker VM. Timings are local QEMU observations, not native-host capacity
or proof that one backend is universally faster. Per-path application overhead
and kernel/background work must remain visible in the resource report.

## References

- [Kernel mount guidance](https://docs.ceph.com/en/umbrella/cephfs/mount-using-kernel-driver/).
- [Secure-key compatibility and migration](https://docs.ceph.com/en/latest/rados/configuration/auth-config-ref/).
- [Linux 6.8 key parser](https://github.com/torvalds/linux/blob/v6.8/net/ceph/crypto.c)
  and [Ceph 21.2 cipher selection](https://github.com/ceph/ceph/blob/v21.2.0/src/auth/Crypto.cc).
