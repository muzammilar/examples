# ZFS

Website: https://openzfs.org/
GitHub: https://github.com/openzfs/zfs

| Example | What it isolates |
|---|---|
| [`storage-lab/`](storage-lab/README.md) | Three ZFS-only setups: mirror member recovery, RAIDZ2 two-member recovery, and resumable snapshot replication/promotion. Real Linux guest kernels, upstream fio checksums and bounded performance tests. |
| [DuckDB integration](../duckdb/zfs-recovery/README.md) | Database/WAL recovery, externally journaled acknowledgements and four remote clients; separate from filesystem correctness. |

ZFS provides local storage redundancy and snapshot transfer. It does not provide
a distributed consensus filesystem or automatic application failover. These
labs share a Docker host, so their virtual disks are separate logical devices,
not independent physical failure domains. Each README lists tested and untested
failures, exact numbers and resource limits.

## Measured checks

2026-11-14–15, Apple M4 Pro, software-emulated Linux guests in ARM64 Docker.
Mirror and RAIDZ2 each verified 512 MiB after one/two member faults respectively,
with clean scrubs. Resumable replication retained all 576 MiB of sent data and
correctly omitted a deliberately unsent 64 MiB tail. Ten negative tests passed.
[Fio results, resource caps and limitations](storage-lab/README.md#measured-fio-performance)
cover warm buffered I/O under emulation; these are not physical-SSD benchmarks.
