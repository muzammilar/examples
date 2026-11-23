# Controlled torn-journal recovery

Use the five-command lifecycle in [README.md](../../../README.md). Before teardown:

```sh
make test-production-recovery
```

This serially runs native capability, filesystem locks, live writer exclusion,
two-crash recovery, isolated restore and this tail test. It creates its own source
with live C++ writers/readers and an external fsynced witness; it never guesses a
previous result directory. To rerun only this case:

```sh
make test-journal-tail-recovery JOURNAL_SOURCE=results/journal-live/<completed-id>
```

The source is this example's output. A clean checkout builds and creates it with
the commands above; no sibling example or host application compiler is needed.
This is an **offline format/recovery diagnostic**, not live availability, physical
disk failure or power loss. Live SIGKILL behavior is separate in [FENCING.md](FENCING.md).

## Fault and recovery procedure

1. Verify source correctness and teardown, and that no journal owner remains.
   Fingerprint its native snapshot/logs.
2. Copy native files into a new case and append exactly **`01 02 03`**, an
   incomplete frame header. Preserve the original.
3. Seed an owned Linux volume. Native `-L` startup must exit **2** with `ERROR
   badtail`, before listening or running the boot script. Preserve failed files
   and prove startup did not alter the damaged log.
4. In another volume, run native `.log.validate` without opening/replaying the
   journal. Require its prefix offset to equal the known original length; prove
   validation changed no native files.
5. Make a **third copy**. Before truncating, verify the original prefix SHA256
   and that the only extra bytes are our exact trailer. Truncate only this new
   copy, fsync and verify equality with the original file. Unknown trailers,
   changed prefixes, wrong offsets and existing repair directories are rejected.
6. Restore that copy on a fresh volume. Require zero replay evaluation errors,
   export both tables and reconcile every seed/customer/ACK body. Prove both the
   original and damaged evidence remain unchanged.

Each native job gets **1 CPU / 4 GiB**, swap disabled, one engine thread and
`RAYFORCE_NO_FSYNC=0`. The validator uses no journal-open flag. Volume ownership,
sampling and fenced export follow [FENCING.md](FENCING.md). The read-only `/data`
mount contains **only an isolation token**: the wrapper verifies that token and
complete listing before starting Rayforce. Original CSVs are available only to
the host oracle. This handles Docker Desktop's `/host_mnt/Users/...` alias while
still rejecting unrelated inputs. Docker copies use `--archive` to retain UID/GID.
Healthy runtime listings verify cleanup; failed export preserves the volume.

## Executed result

**2026-11-21**, Apple M4 Pro, native ARM64 Linux image
`sha256:a3b867026c388fb48c670b399c2f2dad776bea0a793708bf37c7647f61a2997a`,
Rayforce development pin `38fd4be3560d63a5514c1e6883685fe496d45e1a`.
The same six-I64 **2,000,000-trade / 10,000-customer** fixture and SHA256s from
[PERSISTENCE.md](../../snapshots/docs/PERSISTENCE.md) supplied the independent source.

| Correctness observation | Result |
|---|---:|
| Original / damaged journal | 187,784 bytes / 187,787 bytes |
| Validated frames / intact prefix | 1,621 / 187,784 bytes |
| Native exit: damaged / validator / repaired | 2 / 0 / 0 |
| Original bytes discarded / known injected bytes removed | 0 bytes / 3 bytes |
| Restored seed trades / customers / live ACKs | 2,000,000 / 10,000 / 2,262 |
| Missing ACKs / wrong or duplicate bodies | 0 / 0 |
| Applied unacknowledged intents | 0 |

| Native CLI stage | Wall time | Process CPU | Process peak RSS | Cgroup peak |
|---|---:|---:|---:|---:|
| Refused damaged startup | 6,010.703 ms | 4.838 core-s | 241.6 MiB | 299.7 MiB |
| Read-only framing validation | 1.202 ms | 0.001001 core-s | 15.1 MiB | 21.3 MiB |
| Repaired startup and export | 6,710.121 ms | 5.333 core-s | 241.6 MiB | 351.9 MiB |

One observation per stage, not repeated capacity or tail-latency measurements.
Monotonic timers surround the native child, including startup and its script.
Replay repeats earlier logged CSV exports, and the repaired stage then makes new
verification exports: these are **not isolated replay or syscall latencies**.
Whole-container CPU was **6.070 / 0.083 / 6.760 core-s**, including the Python
wrapper and in-container disk sampler; initialization costs are separate in JSON.
Native-process average utilization over those child windows was
**80.5% / 83.3% / 79.5%**, with one core equal to 100%. Short validation timing
is descriptive, not a stable utilization estimate. Whole-container I/O rates in
the compact result use container lifetime, including wrapper startup.

Failed startup already created earlier replayed exports before reaching the bad
tail. It charged **52,957,184 block-write bytes / 53 write operations** despite
rejecting startup. Repaired startup charged **105,914,368 bytes / 98 operations**;
validation charged **0 bytes / 0 operations**. All stages reported **0 block-read
bytes / 0 operations** in the observed cache state. These are cgroup-accounted
I/O; Docker copy/daemon work, physical host I/O, device latency and queue depth
are outside the measurement. Failed replay is not rollback of earlier external
effects. An export file left behind does not prove the service came up.

Native snapshot/log files totaled **96,668,820 bytes** in damaged/validation
copies and **96,668,817 bytes** after repair. Off-path gzip level 1 produced
**22,991,585 / 22,991,585 / 22,991,580 bytes**, about **4.205×**. Compression CPU
costs were **335.015 / 334.868 / 335.184 ms** and wall costs
**335.291 / 335.149 / 335.402 ms**. This is archive compressibility, not native
encoded-column compression.

Observed volume maxima including CSVs were **149,623,164 bytes** (damaged),
**96,669,115 bytes** (validation) and **202,577,210 bytes** (repaired). After volume
deletion, retained host case files used **449,132,109 logical bytes / 449,269,760
reported allocated bytes**. Source fixture/witness storage is separate; reported
blocks are not exclusive physical extents, and sampling can miss transient peaks
or copy overlap. Host controller costs were **2.983 CPU core-s / 151.7 MiB peak
RSS / 23,636.372 ms wall**, including copy/hash/oracle/gzip, excluding CLI
subprocess/daemon/VM costs. No OS-cold cache claim is made.

[TAIL_RECOVERY_RESULTS.json](../reports/TAIL_RECOVERY_RESULTS.json) retains environment,
source hashes, image/limits, fault fingerprints, native outcomes, resources,
I/O/storage/compression and cleanup proof. Selected raw case:
`results/journal-tail/7b4cbea4ae054c6486497c70a84c4ff1/`; fresh source:
`results/journal-live/43062c50ae894f24b520f2fdcd16d45d/`.
Publish explicitly with
`make report-journal-tail TAIL_RESULT=results/journal-tail/7b4cbea4ae054c6486497c70a84c4ff1`.
The recorded six-case serial recovery workflow and **22 controller tests** passed.
The default `make test-production` now adds [interior payload corruption](CORRUPTION.md)
and returns nonzero for its recorded integrity failure; use the recovery-only
target above for the six cases described here. Earlier development
cases remain: two mount-alias safety refusals and a complete tail test against
a different acknowledged source.
The six-case receipt is [PRODUCTION_QUALIFICATION.json](../../../reports/PRODUCTION_QUALIFICATION.json).
All six Python/C++/Rust × engine correctness cases and 18 native negative controls
also passed afterward. Publish before `make docker-down`: the publisher checks
the tested image remains local, source/fixture hashes, limits and healthy cleanup.

## Limits

Only the known injected trailer is removable from a verified copy. This is not
an automatic production repair procedure. Real torn frames can contain ACKs or
ambiguous operations; preserve logs, diagnostics, witnesses and backups before
deciding what can safely be lost. Framing validation does not establish payload
correctness or justify truncating an interior fault. Separate [payload](CORRUPTION.md)
and [header](HEADER_CORRUPTION.md) diagnostics now demonstrate that distinction.
Interrupted live snapshots, physical media loss and matched DuckDB recovery
remain unrun here. This is one-host storage, not HA.

Sources: [journal API](https://rayforcedb.com/docs/namespaces/log/),
[pinned recovery](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/journal.c),
[pinned validator](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/ops/journal.c),
[Docker copy ownership](https://docs.docker.com/reference/cli/docker/container/cp/).
