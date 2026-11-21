# Interior journal corruption: failed integrity, verified backup recovery

**Finding:** the pinned uncompressed `-L` request-journal path accepted a deliberately
selected one-bit price change. Replay reported zero evaluation errors and exported
one wrong acknowledged body. The independent oracle rejected it. This case remains
**FAILED**, even though an untouched backup recovered every correct body afterward.

This is a **logical edit through the file API on a fenced copy**, not physical bit
rot, power loss, random-error frequency or a claim that normal process-crash
durability failed. It does not test resistance to a malicious actor recomputing
checksums. Results apply to the pinned request-journal format/path, not every
Rayforce storage API.

## Commands

Use the five-command lifecycle in [README.md](README.md), then:

```sh
make test-production
```

The default seven-case matrix now includes this adversarial probe and returns
**nonzero on the measured build**. The six recovery cases run first; backup
recovery inside this probe also finishes before its integrity failure propagates.
The Python probe exits **1**; Make propagates failure. Do not read the final error
as an unexecuted test or turn it green by deleting evidence.

Run only the six process/recovery cases with `make test-production-recovery`.
Run this case independently from a completed source with:

```sh
make test-journal-interior-corruption JOURNAL_SOURCE=results/journal-live/<id>
```

The source is created by this example's own recovery workflow. No sibling state
or host application compiler is needed. Its external fsynced witness stays
outside the failed volume.

## What is changed

The controller reads the pinned v3, little-endian framing metadata solely to
locate an **acknowledged insertion in the middle of the active log**. It skips
compressed/nonmatching messages and requires acknowledged insertions on both
sides. Native validation/replay remains authoritative; this is not a replacement
deserializer or client protocol.

Two independent copies receive one byte edit each, with all frame headers,
lengths, order and total file length unchanged:

| Copy | Edit | Native framing validation | Startup/replay |
|---|---|---|---|
| Invalid type | String type `0xf3` → unknown type `0x7f` | Accepts all frames/bytes | Exit **2**; deserialization error; no boot/listener; warns **Do NOT truncate** |
| Valid literal | Last price digit `0x34` → `0x35`, one bit | Accepts all frames/bytes | Exit **0**, boot/listener reached, zero eval errors; **wrong ACK body exported** |

Both validators reported **1,624 frames / 187,996 bytes**, the complete log.
Framing validity did not establish payload integrity. The guarded tail-repair
function rejected both files; neither was truncated. A fresh Linux volume then
restored the untouched snapshot plus journal tail and verified complete outputs.
Original and corrupt native files retain their fingerprints.

Five serial native jobs use uniquely owned Docker-managed Linux volumes,
**1 CPU / 4 GiB**, swap disabled, one engine thread, and explicit
`RAYFORCE_NO_FSYNC=0`. The read-only `/data` mount contains only a verified
isolation token. Native jobs cannot read the original input CSVs; only the host
oracle can. Native state is exported after fencing, and runtime listings verify
all owned containers/networks/volumes were removed. Failed exports retain storage.

## Measured bodies and verdicts

**2026-11-21**, Apple M4 Pro, native ARM64 image and exact source hashes in
[CORRUPTION_RESULTS.json](CORRUPTION_RESULTS.json). Rayforce development commit
`38fd4be3560d63a5514c1e6883685fe496d45e1a`; same seeded six-I64 trade/customer
fixture as [PERSISTENCE.md](PERSISTENCE.md).

| Observation | Result |
|---|---:|
| Complete trade rows compared | 2,002,269 |
| Acknowledged trade bodies changed | **1 — FAILED integrity** |
| Missing/duplicate trade IDs | 0 |
| Wrong customer bodies | 0 of 10,000 |
| Target ID / payload byte offset | 2,002,206 / 94,408 |
| Expected row | `[2002206, 2206, 30, 6, 7, 2304]` |
| Actual row | `[2002206, 2206, 30, 6, 7, 2305]` |
| Untouched backup: seed trades / customers / live ACKs verified | 2,000,000 / 10,000 / 2,269 |
| Backup restore: missing ACKs / corrupt or duplicate bodies | 0 / 0 |

The target was deliberately selected, not a random bit-error sample. A successful
restore does not erase the prior integrity violation. The default production
matrix is therefore **not wholly green under this logical-payload corruption
model**. The six process/recovery cases still passed; this does not establish
general production readiness or physical-media fault tolerance.

## Resource costs

One observation per stage; these are correctness/diagnostic costs, not capacity
or tail-latency benchmarks. Native-child wall includes CLI initialization,
replay and boot-script exports where reached. It is not isolated replay latency.

| Stage | Native exit | Child wall | Child CPU | Child peak RSS | Cgroup peak |
|---|---:|---:|---:|---:|---:|
| Invalid-type framing check | 0 | 1.086 ms | 0.000953 core-s | 15.0 MiB | 21.1 MiB |
| Invalid-type startup | 2 | 2,801.937 ms | 2.209 core-s | 240.5 MiB | 257.9 MiB |
| Valid-literal framing check | 0 | 1.339 ms | 0.001043 core-s | 15.0 MiB | 21.4 MiB |
| Valid-literal startup/exports | 0 | 6,775.875 ms | 5.405 core-s | 241.3 MiB | 358.2 MiB |
| Pristine backup startup/exports | 0 | 6,750.551 ms | 5.386 core-s | 243.2 MiB | 354.3 MiB |

Average child CPU was **87.7% / 78.8% / 77.9% / 79.8% / 79.8%**, with one core
equal to 100%. Very short checks do not establish stable utilization. Whole
cgroup/initialization CPU and sampling data remain separate in JSON; wrapper
and sampler costs are included there. Container I/O rates use container lifetime,
not the narrower native-child timer.

Both successful startup/export jobs charged **105,914,368 block-write bytes**,
with **113 operations** for the corrupt copy and **102 operations** for the backup.
Other jobs charged **0 write bytes / 0 operations**; all read counters were
**0 bytes / 0 operations** under the observed cache state. These are cgroup block
counters, not proof of zero physical host reads. Docker-copy/daemon I/O, physical
device latency and queue depth are unavailable. No OS-cold cache claim is made.

Each native file set totaled **96,669,930 bytes**. Off-path gzip level 1 produced
**22,991,781 bytes** for invalid-type copies, **22,991,767 bytes** for valid-literal
copies and **22,991,769 bytes** for the pristine backup, about **4.205×**.
Per-stage gzip CPU was **334.705–343.667 ms**, wall **334.814–345.256 ms**.
This is archive compressibility, not native encoded-column compression or a
protection mechanism for the original journal.

Retained case files used **695,572,626 logical bytes / 695,787,520 reported
allocated bytes** after volume deletion. Per-stage native/export growth is in
JSON. Reported blocks are not exclusive physical extents; sampling can miss
peaks/copy overlap. Host controller costs were **6.521 CPU core-s / 152.8 MiB peak
RSS / 35,698.942 ms wall**, including copy/hash/oracles/gzip, excluding Docker
CLI subprocess, daemon and VM overhead.

Selected raw case: `results/journal-corruption/33f49d9453db41769e0ef46e77d8995c/`;
fresh source: `results/journal-live/24d6436146ef4967a24bf47691548168/`.
The seven-case run is recorded in `results/journal-production-with-interior.log`
and the checked-in [qualification receipt](PRODUCTION_INTEGRITY_QUALIFICATION.json);
the final case failed integrity as reported. **25 controller tests** passed.
An independent `make test-production-recovery` rerun also passed all six cases,
recovering **2,259 ACKs** with zero missing/wrong bodies. Its receipt retains a
prior setup-only Compose pull failure; retry used the same inspected image hash.
The final `make test` passed all **six Python/C++/Rust × engine cases** and
**18 negative controls** on the full fixture; this is a correctness regression,
not another comparative benchmark.
An earlier independent source reproduced the same one-bit integrity failure;
its raw state is retained too.
Publish the selected evidence before image teardown with
`make report-journal-corruption CORRUPTION_RESULT=results/journal-corruption/33f49d9453db41769e0ef46e77d8995c`.
Publication preserves the **FAILED** verdict and checks source/oracle hashes,
native outcomes, resource budgets, failed-state fingerprints and cleanup.

## Further work

The separate native **AOF API** in the same source pin declares CRC32 protection
over length plus payload. It is not the `-L` request-journal path tested here;
its application integration, corruption detection and recovery remain unrun.
Do not claim the whole system lacks checksums or that AOF fixes this deployment
without testing it. Interior header/version damage, compressed frames, interrupted
snapshots, physical media faults and matched DuckDB recovery remain open.

Sources: [pinned frame layout](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/serde.h),
[pinned decoder](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/serde.c),
[journal validation/replay](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/journal.c),
[distinct CRC-protected AOF format](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/aof.h).
