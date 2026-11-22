# CedarDB/PostgreSQL updates and deletes under analytical queries

Use the five-command [Quick start](README.md#quick-start), with additional tests
before teardown:

```sh
make test-mutable
make test-mutable-recovery
make test-mutable-benchmark  # four short harness checks, not performance qualification
make benchmark-mutable     # full 3-trial, 60s-warmup / 300s-measurement matrix; pending
```

**Current checkpoint: 10,000-row correctness and recovery are tested. The sustained
million-row comparison has not run yet. No new performance winner is established.**
The [result document](MUTABLE_RESULTS.json) records exact case IDs, source/image
hashes, full-body proofs, resources and preserved prototype failures. Existing
[append-only HTAP results](HTAP.md) remain a separate million-row experiment.

## Workload and contract

The [backend contract](mutable-contract.json) defines one Go writer, an independent
Go snapshot reader, and a Go analytical application, built/tested through the
pinned Docker Go toolchain. No host application toolchain is required. Each
database has **4 CPUs / 6 GiB**, each application **1 CPU / 512 MiB**, swap disabled.
Performance cells activate one backend at a time. CedarDB CE `v2026-11-18` and
PostgreSQL `17.6` retain the example's pinned image digests.

Each case owns a new UUID schema. It copies the verified immutable base fixture
into `mutable_orders`, adds a revision field, and creates `mutation_commits`.
The original `orders` table and its histories remain unchanged.

The writer alternates two operations on a selected two-row pair: a real paired
`UPDATE`, then `DELETE` and reinsert. Both change amounts, payload hashes and
revisions. The pair remains in the same analytical bucket, with balanced signed
amount adjustments and unchanged payload length. Each change and its native
commit receipt are one transaction. Analytical queries cover the **actually
changed rows**, verifying all 100 bucket keys/counts/sums/payload lengths against
an independent invariant oracle. This is not arbitrary UPDATE/DELETE HTAP or a
transaction benchmark standard.

An external fsynced intent/ACK journal supplies exact expected bodies. Native
receipts identify applied ambiguous requests; they never become ACKs merely
because they appear in storage. Complete final exports reconcile every row,
revision and receipt, including earlier overwritten mutations. No mutation is
retried. Both backends explicitly use repeatable-read transactions and synchronous
commit settings. Snapshot isolation does not establish serializability.

Independent readers inspect the latest committed pair and an older untouched
pair in one read-only snapshot. They verify four bodies, monotonic commit
watermarks and prior-ACK lower bounds with a **10 ms** shared-VM clock guard.
These composite resident-data reads do not isolate hot/warm/cold latency or prove
general linearizability. Native controls reject writes with SQLSTATE **25006**
and verify complete rollback after SQLSTATE **22012**.

Defaults are **50 mutation transactions/s**, **20 snapshot reads/s**, and a
**10,000-pair** mutable window for a million-row fixture. The functional tests
deliberately cycle **8 pairs**. Arrival is ticker-paced closed loop; backpressure
can drop ticks. Actual completions and failures are reported, not a capacity rate.

## Executed checkpoint

**2026-11-22, Apple M4 Pro, native ARM64 Docker VM.** Each independent case starts
with **10,000 rows**, 10,000 customers, a 256-byte ASCII payload per row and no
nulls. Two-row changes preserve aggregate totals while changing actual data.
The SIGKILL cases hold the real database container down for **5 s** while both
applications continue, then prove new database generations, unchanged surviving
backend/application identities, native recovery and full reconciliation.

| Backend / case | ACKed transactions | Committed transactions | Successful snapshots | ACK loss / wrong or partial rows |
|---|---:|---:|---:|---:|
| CedarDB healthy | 594 | 594 | 237 | 0 / 0 |
| PostgreSQL healthy | 598 | 598 | 239 | 0 / 0 |
| CedarDB SIGKILL | 921 | 921 | 368 | 0 / 0 |
| PostgreSQL SIGKILL | 923 | 923 | 369 | 0 / 0 |

All four cases verify all **10,000 seeded bodies**, every acknowledged receipt
and both operation types. Healthy phases have zero unexpected request errors.
Outages produce errors in both independent applications. Failed/ambiguous counts
and successful/error latency distributions remain separate in the JSON;
per-statement/commit timing records remain in the raw witnesses. The final legacy regression also verifies the original
10,000-row table per backend, native duplicate/missing-key controls, and **19 host
controller tests**. Docker builds enforce Go formatting, vet and Go tests.

| Healthy diagnostic | CedarDB | PostgreSQL |
|---|---:|---:|
| Baseline transaction p50 / p99 | 5.827 ms / 16.040 ms | 1.180 ms / 3.660 ms |
| Baseline four-body snapshot p50 / p99 | 1.889 ms / 4.718 ms | 0.708 ms / 2.940 ms |
| Observed DB CPU across sampled intervals | 14.510 core-s | 6.538 core-s |
| Sampled DB charged-memory maximum | 137.43 MiB | 88.19 MiB |
| Whole-store logical / allocated bytes after case | 27,446,597 / 1,622,851,584 | 68,095,379 / 68,157,440 |
| Verified row-export gzip level 1 ratio | 4.523× | 4.522× |
| Observed DB block read / write delta | 8,671,232 / 18,780,160 bytes | 0 / 3,686,400 bytes |
| Observed block read / write rates | 616,146 / 1,334,448 bytes/s | 0 / 243,220 bytes/s |

These are short functional observations, not repeated performance comparisons.
CPU includes setup/reconciliation and instrumentation in sampled intervals;
reset gaps during crashes are excluded, not estimated. Native counters, cgroup
peaks, application CPU/RSS, block read/write bytes/rates and network counters are
retained per case. Whole-store bytes include the immutable base, mutable tables,
receipts, WAL/engine files and retained prototype state. Allocated bytes are not
native compression ratios. Exact mutable-table compression statistics use table
OID/schema identity; CedarDB statistics cover cold data only. CSV gzip measures
the same verified export, with compression CPU/time recorded separately.
Physical host I/O latency/queue depth and complete CPU totals across a crash are
unavailable. PID 1 RSS is not whole-database memory.
The block windows span **14.073 s** and **15.157 s** respectively, including setup
and verification. Sampled Docker statistics did not expose IOPS in this run;
native `io.stat` cumulative operation counters remain in the raw snapshots.

A subsequent [collector check](MUTABLE_IO_RESULTS.json) samples native cgroup
`io.stat` alongside Docker statistics, retaining each device separately. Both
10,000-row healthy cases pass: **600 CedarDB / 601 PostgreSQL** acknowledged
transactions, zero acknowledged loss or wrong/partial rows, and **20 controller
tests**. Across the setup-through-verification sampled intervals, CedarDB records
**1.016 read / 268.808 write IOPS** and PostgreSQL **0 / 42.832 IOPS**. These short
functional observations validate collection, not relative capacity. Zero reads
mean no charged device reads during those intervals, not no logical reads.
The native block endpoints can share a backing device; do not add them to the
Docker byte counters as if they were distinct traffic. Physical host I/O
latency/queue depth remains unavailable.

The four-cell diagnostic uses **2 s warmup / 10 s measurement / one trial** on
10,000 rows, both alone and under mutations. It proves the orchestration, resource
gates and full-output oracle execute; it cannot support a sustained ranking.
The full target defaults to **12 cells**, with fresh database processes and UUID
schemas, alternating backend/condition order, 60 s warmup and at least 300 s per
measurement. Base volumes and OS caches are retained; no OS-cold claim. A larger
fixture requires verified teardown/recreation first; `up` refuses to overwrite a
retained fixture with a different `ROWS` value.

## Preserved failure and limits

PostgreSQL initially rejected numeric `CASE` branches with SQLSTATE **42804**:
simple-protocol parameters are encoded as text, and otherwise-unknown branches
resolve as text. Explicit `CAST(... AS INTEGER)` fixes the SQL on both backends.
The strong healthy gate rejected that run. An earlier progress-only prototype
incorrectly marked its run passed despite failed UPDATEs; it is explicitly
**INVALIDATED**, with raw state/history retained. Neither is used as passing
qualification. Successful schemas are dropped only after verified external
exports and stopped actors; failed schemas are retained.

Still pending: the repeated million-row benchmark, multi-writer conflicts,
arbitrary changing aggregate totals, permanent deletes, verified cold access,
open-loop saturation, sustained pressure and physical disk/power-loss recovery.
The original append-only `test-production` remains separate from these new targets.

Inspected references: [CedarDB transactions](https://cedardb.com/docs/references/transactions/),
[CedarDB compression metadata](https://cedardb.com/docs/compatibility/system_table/),
[PostgreSQL 17 isolation](https://www.postgresql.org/docs/17/transaction-iso.html),
[PostgreSQL CASE resolution](https://www.postgresql.org/docs/17/typeconv-union-case.html),
[pinned pgx transaction code](https://github.com/jackc/pgx/blob/v5.7.6/tx.go) and
[simple-protocol encoding](https://github.com/jackc/pgx/blob/v5.7.6/values.go).
CedarDB engine source is closed; the example inspects its public APIs/configuration
and the available pinned driver/PostgreSQL source rather than claiming an engine-source audit.
