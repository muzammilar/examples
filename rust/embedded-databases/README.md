# Fjall and redb: durable embedded catalog recovery

## Quick start

1. `make docker-up` — build the pinned Rust image and initialize the example-owned volume.
2. `make up` — seed and verify 100,000 catalog pairs on each engine; retained setup verifies existing data.
3. `make test` — keep independent readers/writers active through each owner SIGKILL, then verify retained reopen and an offline fresh-copy restore.
4. `make down` — reconcile every historical ACK and stop the workload, preserving storage and witnesses.
5. `make docker-down` — verify again, byte-check an external native-state copy, and delete only owned Docker resources.

Prerequisites: Docker Compose v2, Make, **host Python 3.11+**, and a local Unix
Docker API. No host Rust compiler is needed. A small standard-library Python
wrapper holds one host-kernel file lock across every complete Make lifecycle
action, including cleanup. This avoids assuming host and Docker-VM locks are
equivalent. Real competing host processes verify rejection, release and the same
inode/device. Native workloads, controllers, resource sampling and publication
use Rust; `make check-controls` reruns the isolated host-lock controls.

`SEED_PAIRS=100000` is overridable from 100 through 1,000,000 for fresh fixtures;
changing it rejects a retained fixture with a different size. Complete the
lifecycle first when choosing a new size. To reproduce the larger run, use
`SEED_PAIRS=1000000 make up` after fresh `make docker-up`; the later test commands
read the actual retained size. `make test-production` runs the selected crash/
restore checks serially. Run `make report` before teardown to independently replay
the latest cases for the compiled source and publish compact `RESULTS.json`.
Raw data stay ignored.

The entire evaluation service has **2 CPUs / 2 GiB, swap disabled**. It has no
network and exposes no host ports. Owner, application writer, application reader
and test controller are separate Rust processes. They communicate over a private
Unix socket; each library owns its database files in one native process. This is
a process-crash experiment, not replication or independent-host durability.

## Contract and workload

[`contract.json`](config/contract.json) pins the supported operations, consistency,
durability and client reference configuration. Fjall **3.1.13** uses explicit
`PersistMode::SyncAll` on every atomic cross-keyspace batch. redb **5.0.0** uses
explicit `Durability::Immediate` on every two-table write transaction. These are
matching durable atomic mutations; Fjall's batch is **not** a read-modify-write
transaction. We do not claim serializability or general linearizability.

Each pair has an ordered big-endian `u64` key and two distinct **256-byte** ASCII
values, one per keyspace/table. The deterministic synthetic corpus contains
10,000 customer IDs, repeated text and SHA-256-derived suffixes, with no nulls.
The default baseline is **200,000 native records / 52,800,000 logical key/value
bytes** per engine; the export framing is a separate size. This is synthetic
catalog data, not a production-fitted payload distribution.

The writer fsyncs an external intent before calling the owner, then fsyncs its
ACK or ambiguous outcome. Every request gets a new immutable ID; mutations are
never automatically retried. A fsynced recent-ACK marker lets the independent
reader inspect a known recent pair alongside the historical pair at ID 0. Every
read uses one native snapshot for both bodies, records the actual returned body
hashes, and checks exact values. No request's error is treated as proof that its
mutation did not commit.

Before fault injection, both read localities and writes must make progress.
The controller kills the actual owner, waits for **SIGKILL / signal 9**, proves
its PID is absent, and holds it down for **5 seconds**. The same independent
writer/reader processes continue during the outage and native reopen. Recovery
requires new phase-local successful writes and both reader localities; old
pre-fault successes cannot satisfy that gate. Full ordered exports check every
ACK and possible ambiguous intent, both bodies, duplicate/unexpected IDs and
partial atomic visibility. The checker rejects corruption, missing, duplicate,
unknown and partial-pair negative controls.

The owner then closes gracefully. The controller copies its stopped files into
a fresh directory on the same owned volume, checks every copied file's SHA-256, opens a new owner and
repeats complete body verification. This is **offline cold-copy restore**, not an
online backup, an independent backup failure domain, a snapshot RPO test or an
independent-host disaster-recovery proof.
A second native owner must reject with the actual engine's lock error, within a
bounded wait. A separate standard-library cross-process lock control verifies
the mounted Linux volume's exclusion and reported inode/device identity.

## Measurements and result status

**2026-11-22: both the 100,000-pair and million-pair profiles pass.** Native ARM64 on Apple M4 Pro / 48 GiB; Docker 11 CPUs / 24.42 GiB, each complete evaluation container 2 CPUs / 2 GiB with swap disabled. Other isolated functional labs shared this host. Full million-pair evidence: [RESULTS.json](config/RESULTS.json); the separate [100,000-pair checkpoint](config/RESULTS_100K.json) remains unchanged. These are short functional observations, not capacity or recovery rankings.

The fresh larger fixture contains **1,000,000 pairs / 2,000,000 native records / 528,000,000 logical key-and-value bytes per backend**. Seeding uses 1,000 atomic batch commits of 1,000 pairs each; verified pair counts are not individual commit/round-trip counts. The live phase then uses one two-record pair per synchronous request.

| Backend | Live ACKs / ambiguous intents | All ACKed pairs verified | Native records | Successful snapshot requests checked | First write / read after fault | Missing / wrong / partial |
|---|---|---:|---:|---:|---|---|
| fjall | 789 / 492 | 1,000,789 | 2,001,578 | 1,502 | 5.440 s / 5.440 s | 0 / 0 / 0 |
| redb | 778 / 492 | 1,000,778 | 2,001,558 | 1,498 | 5.438 s / 5.438 s | 0 / 0 / 0 |

**One redb ambiguous intent committed both records. It remains unacknowledged**, is reconciled separately, and explains the two native records beyond twice its ACKed-pair count. The other 491 redb ambiguous intents and all 492 Fjall ambiguous intents were absent. No mutation was retried. Both engines recovered every acknowledged body, including all seeded pairs. Fresh-copy restores matched every complete body and exact export checksum.

Recovery times include the deliberate **5 s** outage and native reopen; they are not isolated replay times. Each successful read request checks two bodies in one snapshot, and historical ID 0 is repeatedly read, so it is not a cold-cache measurement.

| Backend / phase | Successful write p50 / p99 | Historical read p50 / p99 | Recent read p50 / p99 |
|---|---|---|---|
| fjall / baseline | 2.326 ms / 3.075 ms | 1.015 ms / 2.299 ms | 1.826 ms / 2.241 ms |
| fjall / recovered | 2.260 ms / 5.745 ms | 1.035 ms / 2.230 ms | 1.873 ms / 2.260 ms |
| redb / baseline | 2.124 ms / 4.043 ms | 0.992 ms / 2.330 ms | 1.807 ms / 2.248 ms |
| redb / recovered | 2.133 ms / 3.245 ms | 1.013 ms / 2.272 ms | 1.799 ms / 2.327 ms |

| Backend | Baseline observed owner CPU | Baseline mean owner RSS / whole-container charged RAM | Original native allocated / logical | Export gzip1 ratio |
|---|---|---|---|---|
| fjall | 0.190 core-s | 268.43 MiB / 1331.36 MiB | 524.83 MiB / 524.76 MiB | 4.903× |
| redb | 0.240 core-s | 136.35 MiB / 2015.07 MiB | 530.70 MiB / 544.00 MiB | 4.903× |

| Backend | Baseline cgroup read / write bytes | Baseline read / write IOPS | Outage ambiguous writes / failed snapshot requests |
|---|---|---|---|
| fjall | 0 B / 1,765,376 B | 0.00 IOPS / 81.15 IOPS | 456 / 910 |
| redb | 0 B / 20,242,432 B | 0.00 IOPS / 788.14 IOPS | 455 / 866 |

These are device 254:0 VM/cgroup counters over the observed baseline intervals, not physical-host I/O measurements. Raw process and cgroup counters are retained. The JSON has all case phase intervals, per-role/whole-container CPU, RSS/charged-memory peaks, disk growth, I/O and compression CPU/RSS/byte costs. Container charged memory includes every role and previously charged file cache. In particular, redb's container can retain cache from the earlier Fjall case and restore copies because both run serially in one container. This is not an isolated engine-memory comparison; owner RSS and whole-container charged memory describe different layers. The publisher replays every returned hash and requires successful reads/writes begun after recovery, not cumulative pre-fault counts.

The 100,000-pair checkpoint verified 101,550 / 101,561 cumulative ACKed pairs across development and final cases (Fjall/redb), with zero loss/wrong/partial bodies. Its final live journals contained 772 / 784 ACKs. It is separate from the fresh million-pair generation above. Six Rust native/oracle/resource controls and two real host-lock controls pass. Source/runtime build failures from development remain preserved and separate from backend outcomes.

Resource collection begins before setup and continues through all fault,
verification, compression and restore phases. It samples approximately once per
second and at phase boundaries: per-process CPU ticks, RSS/native high-water RSS
and I/O; whole-container cgroup CPU, charged memory/peak, throttling and per-device
read/write bytes and IOPS; and whole-owned-database/result logical/allocated bytes.
Missing/partial traversal samples remain unavailable, never zero. Same-PID CPU
intervals exclude process-reset and sampling-edge gaps. Peak counters cover their
native process/container lifetime, rather than individual phase peaks.

The report separates successful/error latency and counts for historical/recent
reads and writes. Writer request latency excludes intent preparation/fsync; rates
include witness costs and the deliberate **10 ms** inter-operation delay. The
Unix transport/serialization is included, so these are not isolated native API
latencies. Failed writes remain ambiguous. Cold/warm cache isolation is unrun;
“historical” does not establish a cold cache. Low-count p99 approaches a maximum.

`gzip -1` on the same verified full-body export measures compressibility and
records bytes, ratio, wall/CPU time and peak RSS, with streamed decompression/
SHA-256 verification. This is **export compression**, not native storage
compression. Fjall enables its default LZ4 codec; native compressed/uncompressed
byte ratios are unavailable in this first profile. redb has no configured value
compression here. Whole-store bytes include native metadata, journals, old data
and retained restore copies; they are not a native compression ratio.

Per-device cgroup counters describe Linux VM accounting, not physical host I/O;
do not sum stacked devices or add them to process-byte counters. Physical-device
I/O latency and queue depth are unavailable. A bare device line has zero counters
in the inspected Linux 6.12 printing code; an absent device or empty file is
unknown. There is no TCP network in this profile; Unix-socket traffic is included
in process/system resource costs.

## Boundaries and remaining tests

- Both are in-process libraries. This project does not invent native replication,
  sharding, Kubernetes operators or multi-master behavior.
- Concurrent isolated functional labs may share the Docker host. One short crash
  observation per backend cannot establish a comparative capacity/recovery winner.
- This tests retained-volume **process** failure. Physical power loss, container
  loss, disk EIO/full disk, file corruption, missing native files and host failure
  remain unrun; a flush call alone does not prove physical power-loss durability.
- Add conflicting updates, in-flight unacknowledged-pair observations, longer-held
  snapshots, compaction/space pinning, maintenance pressure, engine-specific repair
  interruption and separately pinned format migrations in later passes.
- Then run three alternating fresh-process trials at equal isolated budgets,
  single versus batched commits, payload/skew variants, and genuinely controlled
  cache profiles. Keep Fjall transactional modes distinct from plain batches.

## Files and evidence

```text
embedded-databases/
├── Makefile, docker-compose.yml    # owned, bounded five-command lifecycle
├── contract.json                 # checked-in backend/client contract
├── cleanup.sh, environment.sh     # scoped Docker teardown and environment header
├── catalog-workload/
│   ├── Dockerfile, Cargo.toml, Cargo.lock
│   └── src/                      # native adapters, independent actors, oracle/controller/report
├── RESULTS.json                  # compact verified summary, once published
├── .build/                       # ignored toolchain cache and development build logs
└── results/                      # ignored UUID histories, raw samples/exports and native archives
```

No sibling Makefiles, generated state or machine-local source links are required.
Failed cases are never erased or silently reclassified. Cleanup requires full
ACK reconciliation and byte-checked external native-state preservation before
volume deletion; cleanup fails closed on errors. Raw archives stay local and
ignored; only source, this README and compact curated results belong in Git.

## Local raw-evidence archive

After both lifecycle teardowns and publication, **330** raw files containing
**11,588,620,132 logical bytes** were archived into **2,523,001,736 bytes** of gzip
compressed tar. Every member's SHA-256 and the gzip CRC were independently checked
before removing the originals, under the same host controller lock and with no
owned Docker resources remaining. The lock inode is preserved; source `results/`
now contains only that lock.

The local archive, manifest, verification record and one-off archiver are ignored
under `results-archives/88201ad3534e44ea9e63e6253fb6c205.*`. Both fixture sizes'
histories, diagnostic logs, full exports and native-state copies remain there;
only the compact [archive summary](reports/ARCHIVE_RESULTS.json) is tracked. The source
and five-command lifecycle reproduce new experiments. This reduction of visible
source files does not establish exact host physical-block reclamation.
Archiving/verification took **58.548 s**, **57.169 CPU core-s** and **44.34 MiB**
peak RSS: off-path housekeeping costs, not database performance.

## Upstream references and inspected paths

- [Fjall 3.1.13 API/source](https://github.com/fjall-rs/fjall/tree/3.1.13):
  `src/db.rs` snapshots/default Buffer durability, `src/batch/mod.rs` explicit
  persistence before publishing the atomic batch, and `src/locked_file.rs`.
- [redb 5.0.0 source](https://github.com/cberner/redb/tree/v5.0.0):
  `src/transactions.rs` immediate durability/abort, `src/db.rs` open modes and
  `src/error.rs` typed `DatabaseAlreadyOpen`; [design](https://github.com/cberner/redb/blob/v5.0.0/docs/design.md)
  and [migration notes](https://github.com/cberner/redb/blob/v5.0.0/CHANGELOG.md).
- [Linux 6.12 block cgroup counters](https://github.com/torvalds/linux/blob/v6.12/block/blk-cgroup.c):
  `blkcg_print_one_stat` and zero-field omission; Linux `/proc` counters and cgroup
  v2 files are retained raw with sampling timestamps.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`reports/`](reports/): compact tracked result summaries.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
