# Rust embedded database survey

**Research and initial execution pass, 2026-11-22.** This reviews three embedded
candidates, not every repository system. After the CedarDB comparison finished,
[Fjall/redb recovery](embedded-databases/README.md) compiled and ran in Rust with
ordinary Cargo tooling and small native adapters. Both 100,000-pair and fresh
million-pair retained-crash/restore profiles pass, with exact acknowledged-body
reconciliation and explicit resource/cold-cache/physical-durability limits.
Turso remains researched but unrun; no SQLite-compatible replacement is qualified.

Official release metadata identifies Fjall **3.1.13**, redb **5.0.0** and Turso
**0.8.4** as non-prerelease release tags. That is a version-selection starting
point, not our stability qualification. Pin exact crate versions, Cargo.lock and
Docker toolchain/image digests before execution. Recheck release fixes then.

| Candidate | Workload that fits its design | Initial contract and constraints |
|---|---|---|
| [Fjall 3.1.13](https://github.com/fjall-rs/fjall/releases/tag/3.1.13) | Write-heavy ordered KV, atomic batches and prefix/range scans while the LSM flushes/compacts | Explicit `SyncAll` before ACK; one owner process with independent reader/writer threads. A write batch is not a read-modify-write transaction. Use a transactional database mode for transactional invariants. |
| [redb 5.0.0](https://github.com/cberner/redb/releases/tag/v5.0.0) | Transactional catalog changes and snapshot readers over copy-on-write B+trees | Explicit `Durability::Immediate`, one writer first. Separate supported multiprocess modes from multithreading and from replicated HA. New-major migration needs its own correctness test. |
| [Turso 0.8.4](https://github.com/tursodatabase/turso/releases/tag/v0.8.4) | Embedded SQL inventory/catalog transactions and SQLite-compatible file/query behavior | Start with ordinary WAL and verify effective sync/commit settings against SQLite. Keep MVCC/concurrent writers and multiprocess WAL as separate experimental profiles. |

## Source findings that determine tests

Fjall's default writes reach OS buffers, not necessarily durable storage. Its
batch commit writes the journal, optionally persists it, then publishes memory
state; journal-write or persist errors poison the database. Test disk-synced ACKs,
atomic cross-keyspace batches, reopen after SIGKILL, and error/reopen handling.
Later add long-lived snapshots, compaction stalls and bounded injected I/O errors.
Use numeric keys with an explicit ordered encoding. Sources:
[pinned README](https://github.com/fjall-rs/fjall/blob/3.1.13/README.md),
[batch commit](https://github.com/fjall-rs/fjall/blob/3.1.13/src/batch/mod.rs) and
[persist](https://github.com/fjall-rs/fjall/blob/3.1.13/src/db.rs).

redb's transaction constructor selects immediate durability; its setter rejects
non-durable commits in writable multiprocess modes. Version 5 changes several
`Option` encodings: older tables can require `Legacy` types, and custom encoders
must preserve old decoding and change the new type identity. Test snapshot
visibility, aborted transactions, kill/reopen, interrupted repair, independent
backup restore and a separately pinned old-to-new migration with exact bodies.
Measure space pinned by old readers before claiming compaction behavior. Sources:
[transaction code](https://github.com/cberner/redb/blob/v5.0.0/src/transactions.rs),
[design](https://github.com/cberner/redb/blob/v5.0.0/docs/design.md) and
[migration notes](https://github.com/cberner/redb/blob/v5.0.0/CHANGELOG.md).

Turso's manual makes ordinary WAL the default and labels MVCC and multiprocess
WAL experimental. The WAL implementation submits asynchronous sync operations;
inspect the pager/commit caller and verify that the selected durability setting
waits for completion before ACK. Having a sync function alone does not prove it.
First test commit/rollback, duplicate constraints, complete indexed query outputs,
SIGKILL/reopen and checkpointing with live readers/writers. Then investigate
concurrent-write conflicts and crash/checkpoint boundaries in separate profiles.
Sources: [pinned manual](https://github.com/tursodatabase/turso/blob/v0.8.4/docs/manual.md),
[WAL code](https://github.com/tursodatabase/turso/blob/v0.8.4/core/storage/wal.rs) and
[Rust binding](https://github.com/tursodatabase/turso/blob/v0.8.4/bindings/rust/src/lib.rs).
Upstream production usage is not evidence that our selected profile passes.

## Execution and comparison gates

1. Establish a shared byte-key/value catalog subset, deterministic input and an
   independent oracle. Make atomic batch versus transactional mutation semantics
   explicit. Compare Fjall/redb first; add SQLite/Turso for the matching subset.
   SQL joins or LSM compaction are separate capability experiments.
2. Keep independent readers and writers live. An external controller fsyncs
   intents/outcomes outside the killed worker and reconciles every ACK/body and
   possible ambiguous commit. Use one owning database process where required;
   extra containers sharing its files are not replicas. Record elapsed operation
   times, errors and before/during/recovered phases, not just final row counts.
3. Run retained-state setup, graceful reopen, actual process crashes and fresh-copy
   restore before sustained comparisons. Preserve failures and source/version
   evidence; no throughput ranking for a configuration that fails its contract.
4. Once qualified, compare single versus atomic batched commits at matched sync
   boundaries, concurrency and offered load; recent versus historical reads and
   verified cache states; and ranges/scans on the same ordered keys. Use at least
   three alternating fresh-process trials. Measure CPU/utilization, memory,
   compression, disk allocation, read/write bytes and IOPS for every experiment.
   Native compression, input compressibility and storage overhead stay separate.

The [implemented Rust project](embedded-databases/README.md) now tests the selected
single-owner durable atomic-pair subset: native abort/delete/snapshot and ownership
controls, independent live readers/writers through SIGKILL, complete external ACK/
body reconciliation, retained reopen and verified offline fresh-copy restore.
The million-pair cases verify 1,000,789 Fjall and 1,000,778 redb ACKed pairs with zero
acknowledged loss or wrong/partial bodies. One additional redb intent committed
without an ACK and remains separately reconciled. CPU/memory/compression/disk/I/O
results are functional diagnostics under shared-host co-tenancy, not a performance
ranking. Sustained comparisons, pressure/corruption, migrations, longer-held
snapshots and other proposed feature combinations remain unrun. The existing
[production requirements](../AGENTS.md#non-functional--production-tests) still apply.
Turso remains a separate candidate; current work returns to the user's Tigris,
DuckDB and Iceberg priorities before adding more Rust scenarios.
