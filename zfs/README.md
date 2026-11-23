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

The **2026-11-22 default five-command lifecycle passed**, with the live-write
default set to **4 MiB/s** and **35 controls**. Independent readers verified a
complete 64 MiB SHA-256 body **157 times for mirror / 140 for RAIDZ2**, including
each native replacement interval; both 512 MiB fio payloads passed full
verification and clean scrubs. Snapshot recovery again omitted the deliberately
unsent 64 MiB tail. All owned resources were removed after guarded native archival.
CPU, memory, compression, disk bytes and IOPS are recorded in
[`OBSERVER_RESULTS.json`](storage-lab/reports/OBSERVER_RESULTS.json). The earlier explicit-
rate checkpoint remains in
[`OBSERVER_RESULTS_4M_OVERRIDE.json`](storage-lab/config/OBSERVER_RESULTS_4M_OVERRIDE.json).
The archive-checker and host Python-prerequisite failures stay documented.
Native stream checks preserve evidence; their independent restore is untested.
These shared-host functional observations do not establish capacity or
independent-host durability.

## Future work / TODO

Deferred follow-ups are linked below. Each example keeps its concrete checklist
beside the measured results; experiments remain paused.

| Example | Next work |
|---|---|
| [storage-lab](storage-lab/README.md#future-work--todo) | Restore the preserved native send/receive archives into separate owned pools and verify every required body against the independent oracle. |
