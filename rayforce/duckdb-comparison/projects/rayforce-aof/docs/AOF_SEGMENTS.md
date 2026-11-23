# Native AOF segment rotation and recovery boundaries

Use the five-command [Quick start](../../../README.md#quick-start), then:

```sh
make test-aof-segments
AOF_ROWS=10003 make test-aof-segments
make report-aof-segments AOF_SEGMENT_RESULT=results/aof-segments/<explicit-run-id>
```

`AOF_SEGMENT_BYTES=65536` controls the native rotation threshold through Make;
`AOF_ROWS=10000` and `AOF_BYTES=1024` are overridable environment settings. The
controller requires at least three segments and caps total payload at **512 MiB**.
The full serial `make test-production` includes this diagnostic before the
existing corruption probes. `test-production-recovery` keeps its eight cases.
The expanded **12-case workflow** was executed; it completed with the two known
payload/AOF integrity failures and a nonzero Make exit. Healthy recovery and
segment observations do not hide those failures.
The [qualification receipt](../reports/AOF_SEGMENT_QUALIFICATION.json) records both fixtures,
the full workflow, 33 controller tests, six engine/language cases, 18 negative
controls and the final five-command lifecycle; owned resources/image were removed.

This is the separate **opaque event-log API**, not automatic table persistence
or the `-L` IPC request journal. The unchanged C++20 application calls native
open/append/commit/scan/close; Python controls independent stopped-state copies.
Each job owns a Linux volume, one CPU / 4 GiB with swap disabled, an application
writer lock and an isolated empty `/data`. The source commit and verified image
are recorded in [AOF_SEGMENT_RESULTS.json](../reports/AOF_SEGMENT_RESULTS.json).

## Measured observations

**2026-11-22, Apple M4 Pro, native ARM64 Docker Desktop VM**, selected run
`7c13172e506b4826a68d78ad2cdce07c`. Ten native jobs, **10,000 events × 1,024
payload bytes = 10,240,000 bytes**, **100 explicit commit acknowledgements**,
**159 native segments**. The final segment starts at LSN **9,954**. Payloads are
deterministic repetitive ASCII by LSN, deliberately highly compressible; this is
a bounded correctness diagnostic, not capacity or production-corpus performance.
An independent **10,003-event** run also passed: **101** explicit commit receipts
include the final three-event batch; tail deletion loses exactly **49** bodies,
and its intact backup verifies all **10,003**. Raw run:
`results/aof-segments/3eedc2d412474eda998301441dacfc18`.

| Case | Native result and independent check |
|---|---|
| Healthy multi-segment reopen/scan | Next LSN 10,000; all 10,000 bodies/LSNs verified, files unchanged |
| Known `010203` uncommitted tail | Read-only scan returns all 10,000 bodies without changing files; native open removes exactly those 3 bytes, restoring the original complete file inventory |
| One payload-bit edit in sealed segment starting LSN 4,977 | Full native scan returns `RAY_ERR_CORRUPT=10`, exit 2; damaged files unchanged |
| Open that same damaged multi-segment log | Returns success / next LSN 10,000; it checks the final segment, not every sealed file |
| Delete the final single-copy segment | Native open/scan succeeds at next LSN / count 9,954; **46 acknowledged bodies are missing** |
| External witness gate | Rejects the missing 46 bodies before further application writes |
| Intact backup in a fresh owned volume | All 10,000 acknowledged bodies verify; zero missing/corrupt/duplicate records |

Deleting acknowledged data is explicitly expected to cause local loss in this
single-copy shape. The diagnostic passes when it detects the exact loss and
demonstrates backup recovery. It does **not** mean the damaged copy recovered or
that native open detected the loss. Missing tail LSNs are never reused by this
test; it closes the inspected handle without appending. The separate
[single-tail integrity findings](AOF.md) remain **FAILED**.

| Timed native operation | Observations |
|---|---:|
| Seed application lifetime | 278.874 ms |
| Append, 10,000 samples: mean / p50 / p99 | 18.324 µs / 0.458 µs / 866.750 µs |
| Explicit commit, 100 samples: mean / p50 / p99 | 770.641 µs / 523.417 µs / 3,261.958 µs |
| Healthy native open, one sample | 130.792 µs |
| Healthy scan with export callbacks, one sample | 13.514 ms |
| Sealed CRC rejection scan, one sample | 6.247 ms |
| Intact-backup scan with export callbacks, one sample | 14.146 ms |

Rotation is itself a native commit/fsync point, including directory-entry sync;
some append calls therefore include disk-barrier costs. Do not describe these
appends as uniformly buffered or infer durable throughput from their reciprocal
latency. Scan timing includes C++ export callbacks and buffered output, not the
subsequent independent host witness check. One-sample opens/scans have no useful
tail percentile; the 100-commit p99 is descriptive, not an established tail bound.
Nanosecond timer units do not imply nanosecond accuracy.

| Resource / storage metric | Observed value |
|---|---:|
| Seed native CPU / average utilization | 0.038870 core-s / 13.94% of one core |
| Seed observed cgroup CPU, including wrapper/instrumentation | 0.184464 core-s over 0.424680 s container lifetime |
| Seed process peak RSS / cgroup peak | 15.07 MiB / 24.44 MiB |
| Seed sampled charged-memory mean / maximum, 2 samples | 15.28 MiB / 20.38 MiB |
| Healthy scan native CPU / process peak RSS / cgroup peak | 0.014467 core-s / 15.05 MiB / 20.97 MiB |
| Native files before seed | 0 bytes |
| Settled segment files, logical / allocated | 10,322,056 / 10,403,840 bytes |
| Largest native-file observation during seed sampling | 4,161,856 bytes, incomplete peak coverage |
| Stable segments gzip level 1 | 208,877 bytes; 49.417× archive ratio |
| Archive wall / CPU cost | 10.639 ms / 10.640 ms |
| Seed cgroup block read / write bytes | 0 / 10,805,248 bytes |
| Seed cgroup read / write operations | 0 / 351 operations |
| Seed lifetime block write rate / IOPS | 24.265 MiB/s / 826.505 IOPS |
| Other native jobs' block read / write counters | 0 / 0 bytes each, cached observation |
| Host controller wall / self CPU / peak RSS | 19.109 s / 1.615 core-s / 36.625 MiB |
| Retained raw copies, exports and reports on host, logical / allocated | 182,881,459 / 184,152,064 bytes |

The final stable file size exceeds the two sampled seed observations; these short
operations can finish before sampling. Every job retains native cumulative CPU,
utilization, process RSS, cgroup peak, disk bytes, block-byte/IOPS rates and archive
compression costs, plus any sampled mean/peak memory and CPU. Missing samples are
unavailable, not zero. Native-child timers exclude Docker copying/export and
host CLI/daemon work. The host controller self counter excludes CLI subprocess
CPU. Block I/O is Linux VM cgroup accounting, not physical host-device writes;
device latency/queue depth and network costs of a remote deployment are unavailable.
These native children perform local file operations, not network service requests.
Native opaque payloads are uncompressed; the measured gzip ratio is an optional
archive layer, not a native AOF codec or a claim about representative event data.
Host artifact allocation sums per-file `st_blocks`; shared extents can differ
from unique physical host usage. Native volumes are removed serially after export.

## Recovery and scope

Fence the sole writer, preserve damaged files and the external ACK/inventory
oracle, and scan **all segments** before serving an existing log. A successful
native open or plausible next LSN does not establish full integrity. A known
three-byte uncommitted suffix is a different fault from acknowledged file loss.
For missing committed data, reject further writes and restore an intact complete
backup into separate owned storage, then compare every LSN/body with the witness
before promotion. This lab verifies the copy/scan; it does not automatically repair
a damaged authoritative log or test online backup availability.

Source inspection follows the exact pinned
[AOF implementation](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/aof.c)
and [native contract](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/aof.h):
rotation commits and syncs; open surveys the tail; full scans survey every listed
segment. The external witness supplies completeness that a missing file cannot
prove about itself. Native source archives are SHA-verified by this example.

Still unrun: concurrent live scans/commits, SIGKILL during rotation, interrupted
directory publication, missing first/interior segments, multiple writers without
the application lock, disk-full/EIO, independent-host backups and physical power
loss. Same-host copy recovery does not establish replicated durability.
