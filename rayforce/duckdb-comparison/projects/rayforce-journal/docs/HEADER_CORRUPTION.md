# Middle-header damage and an unsafe recovery hint

**Finding:** both one-bit middle-header edits stop native startup, but the error
suggests truncating the journal to its validated prefix. Following that hint on
a separate disposable copy starts successfully and loses **566 acknowledged
rows**. This recovery path remains **FAILED durability**. The example's guarded
repair rejects the edits; an untouched snapshot/tail backup restores every body.

This is an offline logical file edit on a fenced copy, not physical bit rot,
power failure, ordinary process-crash loss or a measured fault frequency. The
truncation is a deliberately unsafe counterexample inside the test, never an
approved recovery command. Original, damaged and backup files remain preserved.

## Commands

Use the five-command lifecycle in [README.md](../../../README.md), then:

```sh
make test-journal-header-corruption JOURNAL_SOURCE=results/journal-live/<id>
```

Create the source through this example's own `make test-journal-recovery`.
The expanded `make test-production` runs this diagnostic before the existing
[payload-integrity probe](CORRUPTION.md). The production matrix also evaluates the
distinct native AOF API and exits nonzero for either integrity failure. `make test-production-recovery`
selects only the six original recovery cases.

## Fault, rejection and recovery

Selected observation: **2026-11-21**, Apple M4 Pro, native ARM64 Docker VM,
Rayforce development commit `38fd4be3560d63a5514c1e6883685fe496d45e1a`,
one CPU/thread, **4 GiB**, swap disabled, `RAYFORCE_NO_FSYNC=0`.
Six serial native jobs use fresh, uniquely owned Linux volumes. Their read-only
input mount contains only a verified isolation token, not the original CSVs.
The witness and independent oracle remain outside those volumes.

The same **187,611-byte / 1,621-frame** uncompressed v3 journal is copied for
each edit. Frame index **811** (zero-based), containing acknowledged ID
**2,002,068**, begins at byte **93,795**. There are valid frames before and
after it. One copy changes one magic-prefix bit; another changes the wire
version from **3 to 7** with one bit. All payloads, later frames and file length
remain unchanged.

| Observation | Result |
|---|---|
| Native startup, each damaged copy | Exit **2**, `ERROR badtail`, no boot/listener |
| Native framing validation, each copy | **811 frames / 93,795 bytes** |
| Native suggested recovery | Truncate at byte **93,795** |
| Example's known-tail repair guard | Rejects both before creating a repair |
| Unsafe cut on another copy | Removes **93,816 bytes**, including committed inserts |
| Unsafe copy startup | Exit **0**, zero evaluation errors |
| Unsafe copy missing acknowledged bodies | **566 of 2,259 — FAILED durability** |
| Unsafe copy surviving bodies verified | **2,001,693**, no wrong/duplicate bodies |
| Untouched backup seed/customer/ACK bodies | **2,000,000 / 10,000 / 2,259** verified |
| Untouched backup missing/wrong/duplicate bodies | **0** |

The original full-witness reconciler rejects the unsafe export. A separate
streaming comparison checks every surviving body against the independently
verified pristine restore and proves the missing IDs are exactly the acknowledged
inserts in the removed suffix. Both customer exports match all 10,000 input
bodies. Matching aggregate totals or accepting a successful startup would miss
this recovery error.

The diagnostic target passes only if native rejection, guard rejection, exact
backup recovery and the predicted unsafe-copy loss are all demonstrated. That
does not label the unsafe recovery successful or qualify the system for arbitrary
media corruption. A framing-valid prefix is not evidence that the remaining
bytes contain only unacknowledged work. Restore from a verified intact backup;
do not use the generic truncation hint for unexplained interior damage.

## Resource and storage costs

One observation per stage, not capacity measurements or stable tail percentiles.
Child wall covers CLI initialization, replay and exports where reached.

| Stage | Child wall | Child CPU | Average CPU, 1 core = 100% | Peak RSS | Cgroup peak |
|---|---:|---:|---:|---:|---:|
| Magic damage startup | 2,697.679 ms | 2.175 core-s | 80.6% | 240.6 MiB | 258.1 MiB |
| Magic prefix validation | 1.077 ms | 0.000960 core-s | 89.1% | 15.0 MiB | 21.3 MiB |
| Version damage startup | 2,693.400 ms | 2.162 core-s | 80.3% | 240.6 MiB | 257.9 MiB |
| Version prefix validation | 1.064 ms | 0.000833 core-s | 78.3% | 15.0 MiB | 21.3 MiB |
| Pristine backup restore/exports | 6,589.703 ms | 5.273 core-s | 80.0% | 241.3 MiB | 358.1 MiB |
| Unsafe prefix restore/exports | 3,344.328 ms | 2.678 core-s | 80.1% | 241.3 MiB | 308.7 MiB |

The shorter unsafe run processes less data and is not a performance improvement.
Native high-water counters cover operations too short for the resource sampler;
very short CPU percentages are not sustained utilization. Cgroup peaks include
the wrapper and charged cache; process RSS measures a different boundary.

Backup startup/exports charged **105,914,368 block-write bytes / 95 operations**;
the unsafe prefix charged **52,940,800 bytes / 47 operations**. Both damaged
startups and validators charged **0 write bytes / operations**. All observed
block reads were **0 bytes / operations** under the recorded cache state, not
proof of zero physical host reads. JSON reports byte/IOPS rates over whole
container lifetime. Device latency, queue depth and Docker daemon/copy I/O are
unavailable; no OS-cold claim is made. These are offline file jobs with no
application network traffic, not network-failure tests.

Each original/damaged native file set used **96,668,068 bytes**; the unsafe set
used **96,574,252 bytes**. Off-path gzip level 1 produced
**22,991,417–22,991,427 bytes** for complete sets (**4.205×**) and
**22,978,215 bytes** for the unsafe prefix (**4.203×**). Gzip cost was
**340.081–342.521 ms CPU / 340.980–343.801 ms wall** per stage. This measures
archive compressibility, not native column encoding or integrity protection.
Maximum observed state including exports was **202,575,521 bytes** for the
backup and **149,512,736 bytes** for the unsafe prefix; short peaks can be missed.

After owned volume removal, retained case files occupied **739,244,226 logical
bytes / 739,508,224 reported allocated bytes**. Source fixture/witness storage is
separate; reported blocks are not exclusive physical extents. Host controller
cost was **7.080 CPU core-s / 151.8 MiB peak RSS / 37,662.762 ms wall**, excluding
Docker subprocess/daemon/VM costs. CPU, memory, raw samples, compression and
storage/IO scopes remain separate in [HEADER_CORRUPTION_RESULTS.json](../reports/HEADER_CORRUPTION_RESULTS.json).

Selected raw case: `results/journal-header/b14884dc55e94046b66c2202ad13fcf8/`;
source: `results/journal-live/e5fe72c3a41b4e67a06f9e6a9e1ab049/`.
Publish explicitly before image teardown:

```sh
make report-journal-header HEADER_RESULT=results/journal-header/b14884dc55e94046b66c2202ad13fcf8
```

Publication checks source/fixture/witness hashes, preserved native files, fenced
exports, equal inspected budgets/image and healthy empty owned-runtime listings.
**27 controller tests** passed, including negative controls for fault location,
unexpected lost IDs and wrong surviving bodies. Failed files are not cleaned up
to obtain a passing result.

The [eight-case qualification receipt](../reports/HEADER_QUALIFICATION.json) records a fresh
full-matrix run: six original recovery cases passed, the header diagnostic
reproduced **564 missing ACKs out of 2,247** on its unsafe copy, and the final
payload probe again found one wrong acknowledged body. All pristine restores
reconciled completely. The full target returned nonzero and verified owned
containers/networks/volumes were gone. Native application sources were unchanged;
the prior six-language/engine and 18-negative-control regression receipt remains
linked from that qualification record.

## Source and limits

Pinned [replay and validation](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/journal.c)
classifies wrong magic/version as `BADTAIL` and prints the prefix truncation hint.
The test confirms that path; it does not establish a general corruption repair.
The [frame layout](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/serde.h)
is interpreted only to locate controlled faults, not to replace native decoding.
Compressed frames, arbitrary header/length edits, CRC-protected native AOF,
interrupted snapshots, physical media failures and matched DuckDB live recovery
remain separate, unqualified cases.
