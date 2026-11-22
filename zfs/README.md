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

The **2026-11-22 follow-up** passed the full serial mirror, RAIDZ2 and replication
suite at `WRITE_MIB_S=4`, plus 34 negative controls. The historical 8 MiB/s default
is not requalified for the longer observer workflow. Independent readers verified a complete 64 MiB
SHA-256 body 165 times for mirror and 152 times for RAIDZ2, including each native
replacement interval; both 512 MiB fio payloads passed full verification and clean
scrubs. Snapshot recovery again omitted the deliberately unsent 64 MiB tail.
Native streams were checked and preserved before pool recycling and teardown;
this is archive preservation, not a tested restore of those streams. CPU, memory,
compression, disk bytes and IOPS are recorded in
[`OBSERVER_RESULTS.json`](storage-lab/OBSERVER_RESULTS.json). The earlier
archive-checker failure stays failed and documented. These shared-host functional
observations do not establish capacity or independent-host durability.
