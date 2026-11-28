# Historical Spark replay beside live CDC

A related project of the [owned CDC analytics lab](../../). Spark recomputes an
older PostgreSQL snapshot while Flink continues applying newer order revisions
and cancellations to the same two RF3 Doris tables. The replay publisher crashes
after native 2PC preparation; recovery commits the original labels and repeats
the same commit. Exact final source readback must pass on every fixed replica.

## Quick start

1. `make docker-up` — build/start the owning CDC lab and build this project's tools.
2. `make up` — provision the shared owned source, stream processing and sink tables.
3. `make test` — run baseline correctness, Spark historical replay and publication recovery.
4. `make down` — reconcile retained source witnesses and stop this owned workload.
5. `make docker-down` — remove the owning lab's infrastructure and example-owned state.

This project shares infrastructure and lifecycle with its parent; do not run it
alongside another project against the same fixture. It has its own application
source, Spark program, controller, tests and curated reports. Relative deployment
paths are resolved from the owning lab through the two explicit Compose files.
Ignored parent `results/historical/<case>/` contains input/output and durable
transaction receipts; this project's ignored `results/` contains controller logs
and all-role resource samples. No other lab's state is required.

Use native ARM64 Docker, Compose and Python 3.10+. Default 1,000,000 orders and
10,000 customers are configurable through Make; the historical interval is the
oldest one fifth of order IDs. Business timestamps are deliberately older while
source revisions increase. Kafka stays three brokers/three separate controllers,
RF3/minISR2; Doris stays three voting FEs and three BEs. No real AWS configuration,
credentials, endpoints or resources are used. The network remains internal.

## What this experiment establishes

The history correction is committed to PostgreSQL with an external intent/ACK
witness and a transactional receipt. A repeatable-read snapshot exports historical
orders and dimensions together. Spark joins them and retains **source versions**;
it never stamps an old snapshot with publication time. A standard-library oracle
compares every Spark output field independently against both exported inputs.
The native Doris sequence column selects the higher source version.

This is replay of a corrected source snapshot, not an independent correction
computed outside the source-of-truth model. CDC may already have delivered those
versions before Spark publishes them. The important race is the subsequent live
updates/cancellations versus stale replay. This does not establish a universal
multi-source merge algorithm. Hard-delete versus backfill and independently
changing dimensions require additional tombstone/version policy and remain unrun.

The publisher prepares two **separate** native transactions, persists their labels
and responses, then waits at a phase gate. The controller verifies PRECOMMITTED,
kills only that publisher, proves source ACK progress, restarts it and commits the
original transactions. This is interrupted publication before commit, not a
lost-response or power-loss test, and is not atomic across the two sink tables.
A repeated commit must reconcile native VISIBLE state without a new batch label.

## Resources and scope

Spark uses one bounded `local[1]` worker, 512-MiB heap, 1,152-MiB container and
0.375 CPU; the Go publisher has 128 MiB / 0.125 CPU. This pair occupies the slot
of the exited SQL setup client. Peak setup/replay caps are 11 CPUs and respectively
21.875 / 22.375 GiB, plus a mandatory 2-GiB reserve. SQL setup must not overlap the
historical phase. Required replica counts and durability are unchanged. This
is a recovery/correctness experiment, not distributed Spark scaling or a capacity
comparison. Spark 4.3.0, Java 17 and Python 3.12 use pinned distribution images;
application publication is Go with maintained native SQL drivers.

All-role Docker samples record CPU utilization/core-seconds, memory, network,
disk read/write bytes and available I/O counters. Source CSV and native ZSTD sink
storage are distinct; a compressed reference file is not a native database
compression measurement. Settled named-volume allocation is recorded separately.
Sampling tails, build/host costs and unavailable physical I/O latency remain gaps.

## Executed results — 2026-11-27, level 3

The million-order / 10,000-customer baseline passed. The first historical case
failed an immediate-visibility harness gate after its publisher crash. Both
original labels were recovered in place, with 999,672 active rows exact on all
three fixed replicas and all 166 source transaction ACKs reconciled. That failed
controller run remains failed in [the curated report](reports/QUALIFICATION.json).

The corrected controller then passed a separate historical case against the
preserved fixture, without reseeding:

| Check | Result |
|---|---|
| Spark snapshot | 199,672 full eight-field rows matched the independent input oracle |
| Source-version race | 10,000 rows advanced beyond their snapshot revisions; 572 were cancelled |
| Publisher fault | Native SIGKILL/exit 137, no OOM; source ACKs advanced 128 → 129 while down |
| Native publication | Both original transactions PRECOMMITTED before/after crash, then VISIBLE |
| Repeated original commit | Native HTTP returned ANALYSIS_ERROR for finished labels; independent transaction readback confirmed VISIBLE |
| Final correctness | Both sinks held exactly 999,428 active rows, every field matched on each of three fixed replicas |
| Durable source receipts | All 330 acknowledged source transactions reconciled; no unresolved source commit |

The Spark application took 22.592 s from its pre-session timer through output
completion; this excludes some process startup/exit work. Preparing the two
batches took 4,894.123 / 6,130.418 ms. The source writer completed 163 live
transactions: p50 1.057 ms, p99 15.464 ms, max 46.467 ms for its timed SQL mutation
and commit, before external witness append. These are transaction timings, not
per-row insert latencies. Warm historical 64-row queries had p50/p99/max
10.904/289.391/289.391 ms for checkpointed and 11.537/147.362/147.362 ms for
asynchronous. Samples are too small to qualify long-tail percentiles or a winner.

The successful whole-case observer measured a lower-bound 194.395 native CPU
core-seconds, 16,419,332,096-byte peak sampled charged memory, 26,189,824 block-read
bytes and 2,512,175,104 block-write bytes. Per-role utilization is in JSON; observed
Spark peak was 38.58% of one core against its 0.375-CPU quota (sample intervals and
quota periods differ). Settled named volumes used 1,485,524,992 allocated bytes /
6,836,328,074 apparent bytes; sparse/preallocated files explain why these differ.
The 9,728,974-byte joined CSV compressed to a 1,896,069-byte zlib reference.
Doris native ZSTD is configured, but its actual uncompressed denominator/ratio is
unavailable here; the CSV ratio is not a database compression claim. Physical
IOPS/service latency are unavailable in these Docker API samples. Builds and
host-controller costs, sampling tails and some short actors are incomplete.

Final `make down` and `make docker-down` passed after retained-data verification;
all owned containers, volumes and networks were removed.

The original five-command attempt reached the controller failure after baseline
success. Its corrected recovery scenario and separate final reconciliation ran
against retained data; this is not a claim that a fresh full lifecycle was rerun
after the fix. [Failure/recovery details](docs/RECOVERY.md) preserve the original
labels and exact diagnosis. Remaining tests are lost commit responses, hard-delete
versus backfill, concurrent independent dimension versions, physical node/disk
loss and independent restore. These are future scope, not prerequisites for a
broader level-4/5 sweep in this batch.


Sources: [Doris sequence ordering](https://doris.apache.org/docs/4.x/data-operate/update/unique-update-concurrent-control/),
[Stream Load 2PC](https://doris.apache.org/docs/4.x/data-operate/transaction/),
[Stream Load](https://doris.apache.org/docs/4.x/key-features/stream-load/).
