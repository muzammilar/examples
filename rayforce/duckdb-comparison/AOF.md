# Native AOF: CRC rejection, silent prefix loss and intact-backup recovery

**Finding:** the separate native AOF API rejects a payload-bit edit, but length
and commit-marker edits can produce successful opens/scans with acknowledged
records missing. This strict integrity evaluation remains **FAILED** under its
explicit logical file-edit fault model. Safe recovery of a known uncommitted
trailer and successful intact-backup verification do not erase those failures.

Scope: one fenced segment containing opaque application events, changed through
the file API after all commit acknowledgements. This is not physical bit rot,
power failure, ordinary SIGKILL durability, HA or automatic Rayforce table
persistence. The `-L` request journal has a different format and is evaluated
separately in [JOURNAL.md](JOURNAL.md), [CORRUPTION.md](CORRUPTION.md) and
[HEADER_CORRUPTION.md](HEADER_CORRUPTION.md). These workloads do not establish a
throughput or transaction comparison between the two APIs.

## Commands and application

Use the five-command lifecycle in [README.md](README.md), then:

```sh
make test-aof-integrity
```

The measured strict-integrity target returns **nonzero** after completing fault
observations and intact-backup verification. The full `make test-production`
also runs this profile. It records both independent payload/AOF failures before
returning nonzero; `make test-production-recovery` runs the six Rayforce recovery
cases plus the two [DuckDB gates](DUCKDB_RECOVERY.md), separately from corruption.

The [C++20 application](analytics-workload/cpp/aof.cpp) calls the public native
`ray_aof_open`, `append`, `commit`, `scan` and `close` APIs directly. CMake builds
it with `-Wall -Wextra -Werror`; standard-library facilities handle payloads,
ownership, export, bounded argument parsing and timing. A Linux `flock` protects
the application's writer: the native AOF API supplies no writer exclusion.
Independent scans are supported by the API; this offline test runs jobs serially.
It does not qualify concurrent writers or live reader availability.

The Python controller creates fresh, uniquely owned Docker-managed Linux volumes
and exports state only after the process is fenced. Every job has **1 CPU /
4 GiB**, swap disabled, dropped capabilities and a read-only image. Its input
mount contains only a verified isolation token. Source, corrupt files, external
fsynced witness and independent body oracle stay outside the tested volume.
Healthy runtime listings must show owned containers/networks/volumes gone.

## Fixture and contract

Defaults are **`AOF_ROWS=10000`**, **`AOF_BYTES=1024`** and
**`AOF_SEGMENT_LIMIT=67108864`** (64 MiB). The initial profile requires one
segment and caps payload bytes at **512 MiB**. It rejects an inconsistent size
configuration before creating the lab. General multi-segment behavior remains
unqualified.
The application additionally bounds segment limits to 4 KiB–256 MiB; a complete
single-segment fixture must fit, so not every row/size combination is valid.

| Contract field | Profile declaration |
|---|---|
| Operations | Append, commit, scan, reopen and close; one sequential writer |
| Healthy visibility | Complete committed prefix, with contiguous LSNs; uncommitted tail may be discarded |
| Durability boundary | Explicit native commit barrier before ACK; fsync enabled; physical power loss untested |
| Retry/idempotency | No mutation retries or native deduplication; application-level dedup is outside this fixture |
| Delete/TTL/transactions | No delete/TTL API or SQL/multi-key transaction claim; opaque append-only records |
| Corruption criterion | Strictly fail successful results that omit/change prior ACKs under the stated post-commit file edits |

| Property | Measured fixture |
|---|---:|
| Unique LSNs | 10,000, IDs 0–9,999 |
| Opaque payload size | 1,024 bytes each |
| Total payload bytes | 10,240,000 bytes (9.765625 MiB) |
| Explicit commit groups | 100 groups × 100 records |
| Native segment bytes | 10,320,800 |
| Record framing | 4-byte LE length, 4-byte CRC32(length + payload), body |
| Commit framing | 8-byte sentinel/magic marker |

Bodies contain a decimal ID prefix and a deterministic repeating ASCII pattern.
This is deliberately compressible synthetic data, not a production corpus or
random payload distribution. Seed receipts are emitted only after a successful
native commit barrier. The controller fsyncs the complete acknowledged prefix,
group receipts and every expected payload hash before injecting any offline
fault. A fresh native reopen and all-body scan must pass first.

The scan application exports actual native callback bytes, without regenerating
expected values. The verifier reads the saved witness from disk and independently
checks every LSN and complete body against its expected size and SHA-256,
including duplicates, unexpected IDs, missing records and wrong content.
A success code or a matching total alone cannot satisfy this oracle.

## Fault observations

Each edit is applied to a separate copy. Open and direct scan use independent
copies, so a mutating reopen cannot conceal the original direct-scan outcome.
Successful opens receive another full-body scan on their own exported state.
Original and damaged source segments remain byte-for-byte preserved.

| Fault | Native open | Independent direct scan | Missing acknowledged bodies |
|---|---|---|---:|
| One payload bit | Rejects `RAY_ERR_CORRUPT` (10), exit 2; no native-file change | Same rejection | No successful incomplete result |
| One length bit, 1,024 → 1,025 | Success; truncates to prefix, next LSN 5,000 | Success, 5,000 records; source unchanged | **5,000 — FAILED** |
| Middle commit-marker magic bit | Success; truncates to prefix, next LSN 5,000 | Success, 5,000 records; source unchanged | **5,000 — FAILED** |
| Last commit-marker magic bit | Success; truncates to prefix, next LSN 9,900 | Success, 9,900 records; source unchanged | **100 — FAILED** |
| Known uncommitted three-byte trailer | Success; removes exactly three bytes, restoring original SHA | Success, all 10,000 records; source unchanged | **0** |
| Untouched backup, fresh volume | Independent scan of the intact native copy | All 10,000 complete bodies correct | **0** |

No wrong surviving bodies or duplicate IDs were observed. The lost-ID sets are
reported individually in JSON. A reported next LSN below the external ACK prefix
is unsafe for application resumption; this test does not append new data to a
damaged/recovered copy or qualify LSN reuse after corruption.

The native survey recognizes commit markers while traversing framing. In the
pinned source it can stop at an invalid marker or incomplete interpreted record;
open then truncates to the last recognized commit. This matches the observed
prefixes. CRC protection of payloads does not by itself prove complete recovery
of this deliberately damaged file. A real crash that never acknowledged a tail
and later damage to acknowledged framing need different recovery evidence.

## Measured timing and resource costs

Selected **2026-11-21** observation: Apple M4 Pro, native ARM64 Docker VM,
Rayforce development commit `38fd4be3560d63a5514c1e6883685fe496d45e1a`.
[AOF_RESULTS.json](AOF_RESULTS.json) pins the exact final image, compiler inputs,
host/runtime, source hashes, raw counters, sampling and all 18 serial jobs.
These are bounded correctness/diagnostic costs, not sustained capacity results.

The C++ application times native calls with `steady_clock`; the native wrapper
reports a **1 ns nominal monotonic clock resolution**, which does not establish
1 ns measurement accuracy. Append timing excludes payload generation and is
**buffer admission**, not durable-record latency. Commit is the explicit barrier
for the preceding group. Scan timing includes buffered export callbacks and
excludes the final application-side stream flush; whole-child wall includes it.

| Native operation during seed | Samples | Mean | p50 | p99 | Maximum |
|---|---:|---:|---:|---:|---:|
| Buffered append, 1,024-byte body | 10,000 | 0.803 µs | 0.458 µs | 4.167 µs | 71.000 µs |
| Commit barrier, 100 records | 100 | 571.516 µs | 545.709 µs | 1,065.708 µs | 1,479.125 µs |

Percentiles are nearest rank within this one short process. The 100-sample p99
is near the maximum, not established sustained tail behavior. No records/s rate
is inferred by inverting these timings; commit batching and workload overhead
make that a different measurement.

| Independent native call | Observed call time, one sample |
|---|---:|
| Healthy committed reopen | 5.861 ms |
| Healthy full-body scan/export | 12.711 ms |
| Payload-corrupt open rejection | 5.932 ms |
| Length-corrupt open, 5,000 ACKs lost | 4.268 ms |
| Middle-marker open, 5,000 ACKs lost | 3.162 ms |
| Last-marker open, 100 ACKs lost | 5.835 ms |
| Known-trailer recovery open | 5.590 ms |
| Intact-backup scan/export | 12.405 ms |

Faster incomplete recovery processes fewer records and is not an improvement.
Singleton calls have no meaningful latency distribution. Inputs were copied to
fresh volumes but may remain in the VM page cache; fresh processes do not prove
OS-cold storage.

| Stage | Whole-child wall | Child CPU | Child lifetime peak RSS | Cgroup peak |
|---|---:|---:|---:|---:|
| Seed: 10,000 appends / 100 barriers | 83.524 ms | 0.029144 core-s | 15.1 MiB | 30.3 MiB |
| Healthy reopen | 6.955 ms | 0.006760 core-s | 15.1 MiB | 11.1 MiB |
| Healthy scan/export | 13.833 ms | 0.013589 core-s | 15.1 MiB | 21.0 MiB |
| Payload rejection, open | 6.904 ms | 0.006727 core-s | 15.1 MiB | 11.0 MiB |
| Length-corrupt open | 5.298 ms | 0.005106 core-s | 15.1 MiB | 17.4 MiB |
| Middle-marker open | 4.341 ms | 0.004123 core-s | 15.1 MiB | 10.9 MiB |
| Last-marker open | 6.781 ms | 0.006615 core-s | 15.1 MiB | 10.9 MiB |
| Known-trailer open | 6.425 ms | 0.006293 core-s | 15.1 MiB | 11.1 MiB |
| Intact-backup scan/export | 13.413 ms | 0.013252 core-s | 15.1 MiB | 21.6 MiB |

Average child CPU, with one core = 100%, was **34.9%** for seed and
**95.0–98.8%** for the other tabled jobs. All 18 children used **0.187215 CPU
core-s**. Child lifetime RSS includes the process/exec boundary; it is neither
C++ heap size nor an isolated post-exec peak. Shared resident pages and cgroup
charging have different scopes, so charged memory can be below reported RSS.

Per-job permission initializers use **1 CPU / 512 MiB**, complete and are removed
before the native 4-GiB job. Their recorded cgroup CPU totaled **1.104489 core-s**;
charged memory peaks were **9.87–9.95 MiB**. Native-job wrapper/sampler cgroup CPU
totaled **1.759031 core-s** and includes the native child; do not add it again to child
CPU. Host controller cost was **1.684432 CPU core-s / 111.1 MiB peak RSS /
29,722.680 ms wall**, including oracles, hashing, copy control and compression.
Publisher rescans, Docker CLI/daemon and VM overhead are outside those counters.

Seed charged **10,727,424 block-write bytes / 206 write operations**. Other
recorded jobs charged **0 write bytes / operations**; all read counters were
**0 bytes / operations** under this cache state. JSON includes byte/IOPS rates
over container lifetime. These are sampled kernel cgroup counters, not logical
engine traffic or proof of zero physical I/O. Copy/daemon work, delayed writeback,
device latency, queue depth and whole-VM peak use remain measurement gaps. Offline
jobs have no application network traffic; this is not a network-failure test.

Complete native segments were **10,320,800 bytes**, versus **5,160,400 bytes**
after the length/middle-marker reopen and **10,217,592 bytes** after last-marker
reopen. Source input sizes are known before each job; the sampler can miss the
pre-truncation peak. Maximum observed state including scan exports was
**20,619,693 bytes**. Retained case files occupied **300,807,263 logical bytes /
301,244,416 reported allocated bytes** after volume deletion; blocks are not
exclusive physical extents.

Off-path gzip level 1 compressed the intact segment to **182,532 bytes**
(**56.542×**), the half-prefix to **91,263 bytes** (**56.544×**) and the
9,900-record prefix to **180,715 bytes** (**56.540×**). Compression cost per job
was **2.767–6.080 ms CPU / 2.767–6.628 ms wall**. This reflects the repetitive
fixture and archive compression, not a native AOF codec or integrity protection.

Selected raw case: `results/aof/a2422af7f624497ba4e3681796c80541/`.
Publish before image teardown:

```sh
make report-aof AOF_RESULT=results/aof/a2422af7f624497ba4e3681796c80541
```

Publication rescans every reported export, checks every controlled edit and
preserved file/witness hash, verifies timing bounds/sample counts and I/O rate
arithmetic, and validates effective budgets, image equality, fencing and healthy
empty owned-runtime listings. Prior runs remain raw evidence, including the
initial low-precision singleton-mean formatting; final means retain sufficient
precision to stay within their exact min/max bounds.

The [final nine-case receipt](AOF_QUALIFICATION.json) records **31 controller
tests**, the six recovery cases, the header diagnostic, and both independently
failed payload/AOF profiles on the final image. The runner correctly returned
nonzero. Normal lifecycle testing passed all **six Python/C++/Rust × engine
cases** and **18 negative controls** on the two-million-trade fixture. Two raw
resource-sampler execs raced exiting jobs; the controller confirmed stopped
state and retained the unavailable samples, while native exit/fencing, high-water
counters and full-body results completed separately.

## Recovery procedure and limits

1. Fence the exact writer and preserve native files plus the external witness.
   Do not reopen unexplained damage on the only copy: open can truncate it.
2. Treat a native success/scan count as insufficient. Reconcile every acknowledged
   ID/body against the independent witness. Read-only scans also returned older
   prefixes successfully in these cases.
3. Restore an intact backup into separate owned storage, keep the original source
   fenced, and verify complete bodies before allowing new writes. This test's
   backup contains all 10,000 acknowledgements, so observed restored loss is zero.
4. If a backup is older, quantify its actual RPO and recover missing bodies from a
   trusted full-data source where available. A hash witness detects loss; it cannot
   reconstruct arbitrary missing payloads by itself.

This backup is an offline same-host native copy, not a supported online backup
protocol or independent-host disaster recovery. Ordinary AOF process-crash and
live concurrent scan/write tests, rotation/non-tail corruption, disk-full and
pressure, interrupted backups and sustained comparative performance remain open.

Sources: [public AOF contract](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/include/rayforce.h),
[AOF format and ownership](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/aof.h),
[survey, open and scan](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/aof.c),
[native CRC32](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/core/crc32.c).
