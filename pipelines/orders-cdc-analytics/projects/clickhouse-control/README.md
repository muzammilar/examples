# Native ClickHouse CDC versus Flink → Doris

The same million-order Kafka input passed exact eight-field validation on all
three copies of both backends. A ClickHouse replica crash also passed live
quorum-write recovery. Short serving-query timings varied sharply after a Doris
restart: **there is no qualified overall performance winner**.

## Quick start

1. `make docker-up` — build tools and create the owning local CDC infrastructure.
2. `make up` — install the source, streaming job and comparison configuration.
3. `make test` — fence one complete input, compare serially and test replica loss.
4. `make down` — verify retained data, archive the consumed tape and remove application state.
5. `make docker-down` — remove the owning lab's containers, volumes and network.

Run from this directory. Requires ARM64 Docker, 11 CPUs, approximately 24 GiB
Docker RAM and the checked disk headroom. This project explicitly shares the
[parent CDC lab](../../): do not run it alongside the parent or historical-replay
project. No real cloud credentials, endpoints or host AWS configuration are used.
`make serving-failure`, `make verify`, `make resources`, `make report` and
`make status` are independent entry points. `make test-production` runs the
bounded serving failure, not every unimplemented production test.

## What is being compared

Both paths consume the **same completed backlog from its beginning**, not two
independently generated datasets:

- PostgreSQL → Debezium/Kafka → one checkpointed Flink current-customer join →
  Doris prejoined serving rows.
- The same Kafka topics → native ClickHouse Kafka engine → replicated raw history
  and LSN-versioned orders/customers → current-customer join at query time.

The initial empty parent job is cancelled before generation. Its asynchronous
sink is absent from the measured job and remains empty. Source mutations are
revoked after generation; topic ends and both groups' committed offsets are
verified. One million orders and 10,000 customers receive 100 dimension updates,
1,000 hard deletes and 1,000 cancellations. The fence contains **1,012,100 unique
CDC events**, with **44 source transaction acknowledgements**. Both backends
return **998,000 active rows**, all eight fields exact on every fixed replica.
Per-key conflicting LSN ties: **zero**. This corpus does not establish a universal
LSN versioning scheme for multiple same-key changes inside one transaction.

The source fingerprint is
`e6594f7267ab64f57111792932413d2fa232ea5375f306e3ec168b3ff8e18f27`.
Both groups reached zero lag at all five fenced partitions. The ignored full
input archive contains 1,012,100 contiguous topic/partition/offset records and
original value bodies: 730,421,694 bytes before gzip, 41,322,644 after.
Its SHA-256 is `ca0a8b8cd42df0c6729bec0bf56e66d04184af4e8f144f7b05aea3269d48d15b`.
This is an auditable consumed tape, **not an independently restored Kafka backup**.

## Architecture and resource fairness

Each serving backend is capped at **4.5 CPUs / 12 GiB total**:

| Backend | Placement and limits |
|---|---|
| Doris 4.1.5 | Three FE voters, 0.5 CPU/1,280 MiB each; three BEs, 1 CPU/2,816 MiB each; eight buckets/tablets per table, RF3 |
| ClickHouse 26.11.4.11 | One shard with three replicas, 1.25 CPU/3,584 MiB each; three Keeper voters, 0.25 CPU/512 MiB each |

This is a three-copy control, **not a sharding comparison**. All roles share one
Docker VM; independent hardware/AZ/disk tolerance is unproven. Flink is stopped
for both serving measurements. Including common Kafka, source, Connect and query
actors gives **7.75 CPUs / 16.875 GiB**, with a further 2-GiB reserve. Doris's
processing phase additionally uses Flink's **3 CPUs / 4.5 GiB**; do not infer equal
end-to-end ingestion cost from equal serving caps. The Docker VM had 11 CPUs,
24.42 GiB RAM and ARM64 Linux 6.12.76. Query fan-out/per-role caps differ.

ClickHouse uses immutable IDs as ReplacingMergeTree keys, no time partitions,
source LSN versions and retained tombstones. `FINAL` resolves versions before
filtering cancelled/deleted rows and joining unique current customers. An
insert-triggered customer join would leave old orders stale on customer changes.
Raw history includes original payload and Kafka position. Ingestion is
at-least-once; no exactly-once claim. Quorum two and fsync settings were read back.
Kafka blocks cap at 10,000 messages with a one-second flush; broken rows are not
silently skipped. ZSTD level 3 is configured. No forced `OPTIMIZE FINAL` or
unqualified tombstone cleanup is used.

These choices apply the `decision-late-arriving-upserts`,
`schema-pk-plan-before-creation`, `schema-partition-start-without`,
`insert-batch-size` and `insert-optimize-avoid-final` guidance. See upstream
[ReplacingMergeTree](https://clickhouse.com/docs/guides/replacing-merge-tree),
[Kafka engine](https://clickhouse.com/docs/engines/table-engines/integrations/kafka),
[PostgreSQL CDC metadata](https://debezium.io/documentation/reference/3.7/connectors/postgresql.html)
and the [pinned stable release](https://github.com/ClickHouse/ClickHouse/releases/tag/v26.11.4.11-stable).
Native Kafka source inspection confirmed execution before offset commit; this
ordering alone does not eliminate duplicate delivery after a crash.

## Measured query timings

Same Go MySQL-wire driver and PostgreSQL oracle; three fresh-connection trials
per family, ten discarded warmups and 30 measured warm queries per trial. Numbers
below are **median of the three trial p50s, milliseconds**. New connections are
not physical disk-cold reads. Ninety warm samples do not qualify long-tail or
sustained cluster capacity. Connection setup is measured separately in the report.

| Query | ClickHouse | Doris after retained restart | Earlier settled Doris diagnostic |
|---|---:|---:|---:|
| Existing order | 3.239 | 87.581 | 9.653 |
| Historical 64-order read | 13.780 | 88.990 | 47.999 |
| Region count/sum | 67.824 | 82.687 | 19.071 |

The first two columns use the identical reader source manifest. The earlier Doris
reader predates an unused live-probe addition and is retained separately. Doris
point trial p50s after restart were **91.819, 87.581, 9.192 ms**; historical reads
were **92.763, 88.990, 47.668 ms**. During the final query interval FE1 was CPU
throttled in **211 of 212 observed periods** at its 0.5-CPU cap. This is an
association, not an isolated cause. Startup/background work, quota allocation and
lifecycle history confound a stable ranking. Doris prejoins while ClickHouse
joins on reads; these are architectural tradeoffs, not interchangeable engine
scores. All measured regimes, including bad timings, remain in the report.

## Failure and recovery

A retained-disk SIGKILL of `ch3` was held for 35 seconds. All four replicated
tables observed only two active replicas after session expiry. A live writer
committed **256 new eight-row quorum batches / 2,048 rows**; ACK count advanced
from 7 before failure to 131 after expiry and 134 while the replica remained down.
All 2,048 bodies matched on all three copies after return: **zero missing,
partial or ambiguous writes**. The fsynced intent/ACK witness is outside the DB.

A simultaneous reader completed **275 exact historical queries in 60 seconds**,
zero errors: p50 **16.857**, p99 **27.790**, max **37.891 ms**. Write batches:
p50 **83.896**, p99 **131.740**, max **157.164 ms**. This fault workload differs
from the healthy queries; do not treat its timing delta as a controlled comparison.
The analytic source was fenced: this proves direct quorum-write/replica recovery,
not live CDC mutation recovery, endpoint failover or permanent disk replacement.

The initial portable-SQL alias failure, Keeper memory refusal, teardown controller
error and retained FE startup deadlock remain failed phases. See the
[recovery runbook](docs/RECOVERY.md) for symptoms and verified in-place recovery.
Final retained checks and teardown passed; all owned infrastructure was removed.
The final automated retained-start helper follows the manually verified recovery
sequence; a fresh full lifecycle after that helper change was not repeated.

## CPU, memory, compression and storage

Selected **whole-phase diagnostics**, including common actors and verification;
these are not query-only costs or equal ingestion-cost windows:

| Window | CPU core-seconds | Peak summed charged memory | Disk read / write bytes |
|---|---:|---:|---:|
| Source and Doris ingestion | 183.104 | 14,118,633,472 | 33,755,136 / 1,322,119,168 |
| Qualified ClickHouse control | 85.145 | 7,223,390,208 | 47,112,192 / 504,016,896 |
| Replica fault | 73.864 | 7,054,462,976 | 20,480 / 122,376,192 |
| Retained archive/restart/validation | 224.957 | 10,494,451,712 | 5,959,680 / 521,482,240 |

Sampling gaps make these lower-bound observations. Native per-role CPU, memory
peaks and disk I/O counters are retained separately. ClickHouse server native
peaks were 1,445,814,272 / 1,199,124,480 / 967,462,912 bytes; the last restarted
role has a different counter epoch. Keeper peaked at 288,153,600 / 302,223,360 /
305,602,560 bytes after rebudgeting.

One-copy raw history: **28,931,932 compressed / 591,215,437 native uncompressed
column bytes (20.44×)**. Typed state: **8,930,996 / 30,215,033 (3.38×)**.
Native column encoding is not logical JSON size; RF3 and the extra audit history
are separate costs. Doris ZSTD was configured, but no comparable native
uncompressed-byte measure was obtained: **no compression winner**.
A pre-cleanup volume snapshot measured Doris **51,691,110 apparent /
293,265,408 allocated bytes**, ClickHouse plus Keeper **407,997,564 /
1,096,278,016**, and Flink state **79,813,938 / 79,872,000**. These live file-block
sums include logs, metadata, old rowsets and preallocation; they are not unique
physical extents or codec-only footprints. I/O byte and operation counters are
reported independently from these storage snapshots.

## Files and remaining scope

- `orders-comparison/`: native Go query/oracle/quorum-writer application and Dockerfile.
- `sql/`, `config/`, `compose.yaml`: both data paths, contract, effective role budgets.
- `scripts/`: lifecycle, input-position audit, exact tape archive and result curation.
- `tests/`: archive completeness/conflict/failure-publication checks.
- [reports/QUALIFICATION.json](reports/QUALIFICATION.json): curated passed and failed evidence.
- `results/`, `.build/`: ignored raw journals/tape/builds; never commit generated bodies.

Future optional work: settled matched warmups and role-budget sensitivity before
a performance ranking; physical cold reads, concurrent capacity, prejoined
ClickHouse control, live CDC changes during faults, endpoint routing, quorum/disk
loss and independent restore. These do not reopen the completed level-3 Flink or
Doris deployment examples. No partitions, upgrades, power-loss durability or
independent backup/restore qualification is claimed here.
