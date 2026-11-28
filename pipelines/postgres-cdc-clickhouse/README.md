# PostgreSQL snapshot → WAL → Kafka → ClickHouse

**Passed 2026-11-27 (Pacific):** million-order snapshot/WAL overlap and retained
Connect crash recovery. The first failed run and controller fixes are documented;
this is a focused level-3 recovery evaluation, not a capacity ranking.
This lab isolates snapshot/WAL overlap and retained Kafka Connect recovery from
the earlier [settled-input comparison](../orders-cdc-analytics/projects/clickhouse-control/).

## Quick start

1. `make docker-up` — build the Go actors, check budgets and start the owned infrastructure.
2. `make up` — seed the source and install replicated raw/current ClickHouse tables.
3. `make test` — overlap writes with snapshotting, interrupt Connect, recover and reconcile.
4. `make down` — verify the complete saved ACK history and drop owned application state.
5. `make docker-down` — remove the owned containers, network, volumes and local workload image.

Requires Docker Compose, Python 3.10+, 11 Docker CPUs, about 20 GiB Docker RAM
including reserve, and at least 12 GiB free Docker disk **after pulling images**.
Default fixture: **1,000,000 orders / 10,000 customers**; override `ORDER_COUNT`
and `CUSTOMER_COUNT` through Make. Source replicas/HA are not part of this case.

## Data path and contract

PostgreSQL → Debezium 3.7.1.Final → three Kafka brokers plus three separate KRaft
controllers → one ClickHouse shard with three data replicas and three Keeper
voters. No host ports, real credentials, public-cloud storage or AWS configuration.
The Go applications are Compose-owned services, built using the local Dockerfile.

The producer records intent before each source transaction and fsyncs its external
witness after ACK. Each transaction inserts an immutable order and receipt, revises
a different existing order, and sometimes deletes an older order or changes a
customer region. One mutation per key/table/transaction bounds the LSN version
contract; arbitrary repeated same-key changes inside one transaction are not
qualified by this fixture.

Raw CDC retains original envelopes and Kafka positions. LSN-versioned
ReplacingMergeTree tables keep deletes as tombstones. Current queries resolve
versions with `FINAL` before filtering or joining current customer state. An
insert-triggered customer enrichment or sum of every order revision would be
incorrect for this workload. No exactly-once or cross-table atomic visibility
claim: the target converges at the explicitly fenced source/Kafka cut.

The controller requires observed snapshot records while new source ACKs advance,
then both a last-snapshot record and streaming changes. It SIGKILLs the actual
owned Connect container, proves its exit and the inactive retained replication
slot, holds it down while source ACKs continue, and restarts the same worker.
It must not increase the snapshot-record count after restart. Native consumer
offsets and complete Kafka positions must reach the final fence. All eight output
fields match independent source SQL on each fixed ClickHouse replica, and every
external ACK retains its original inserted body and transactional source receipt.

The reader checks an immutable historical range continuously; incomplete initial
snapshot visibility is recorded separately from wrong answers. It does not
establish transactional atomic visibility of changing rows during CDC lag.

## Versions and design sources

PostgreSQL **18.7** is the current minor on the newest major listed by stable
Debezium 3.7's compatibility matrix. PostgreSQL **19.0** is newer, but is not
listed there; this is an explicit supported-combination choice, not a claim that
19 was tested or is incompatible. Kafka **4.3.2**, ClickHouse/Keeper
**26.11.4.11**, Go **1.27.4** and maintained pgx **5.13.0** are pinned locally.
Image digests and effective runtime settings belong in the execution reports.

Per `decision-late-arriving-upserts` and `insert-mutation-avoid-update`, use versioned
append state instead of heavy update mutations (**official**). Immutable-ID keys,
no time partitions and current-customer joins are workload-derived choices
(**derived**), applying `schema-pk-plan-before-creation` and
`schema-partition-start-without`. Kafka blocks cap at 10,000 messages and flush
at one second, per `insert-batch-size`; this setting is not a measured capacity.

Sources: [Debezium compatibility](https://debezium.io/releases/),
[PostgreSQL releases](https://www.postgresql.org/support/versioning/),
[connector snapshot/WAL behavior](https://debezium.io/documentation/reference/3.7/connectors/postgresql.html),
[ReplacingMergeTree](https://clickhouse.com/docs/guides/replacing-merge-tree),
[Kafka ingestion](https://clickhouse.com/docs/engines/table-engines/integrations/kafka).

## Files and scope

- `orders-workload/`: Go source, exact streaming oracle, decoder controls and Dockerfile.
- `config/`, `sql/`, `compose.yaml`: owned source, Kafka and ClickHouse topology.
- `scripts/`: lifecycle, fault control, native observations and resource sampling.
- `tests/`: false-success HTTP and foreign-ownership rejection controls.
- `reports/`: compact curated outcomes; `results/` is ignored raw evidence and witnesses.

Level 3: focused correctness/recovery, operation timing and whole-stack CPU/memory,
compression, disk bytes/space/I/O and network observations. A rate-limited source
writer is a recovery diagnostic, not a sustained ingestion-capacity winner.
Permanent WAL/disk loss, failover of PostgreSQL, schema evolution, arbitrary
same-key transaction sequences and simultaneous multiple failures remain outside
this dedicated handoff case. Do not erase a failed fixture or its ACK witness;
use retained-state diagnosis/verification rather than rerunning seed.


## Measured results

The corrected five-command lifecycle completed, including a second exact
reconciliation before teardown. [Curated evidence](reports/QUALIFICATION.json)
includes the failed first case; [recovery notes](docs/RECOVERY.md) explain it.

| Observation | Corrected run |
|---|---:|
| Seed | 1,000,000 orders / 10,000 customers |
| Snapshot records / last marker | 1,010,018 / 1 |
| Source ACK progress during observed incomplete snapshot | 39 → 51 |
| Live source transactions acknowledged | 1,310 |
| Source ACKs while Connect was held down | 119 additional |
| Proved connector outage hold | 12.434 s |
| Restart → native task RUNNING | 58.205 s |
| Final active rows on each of three replicas | 1,000,861, all eight fields exact |
| Missing/wrong acknowledged inserts | 0 |
| New snapshot after retained restart | 0 |
| Historical reads during connector outage | 60 successful, p50 6.723 / p95 8.785 ms |
| Source transactions during outage | p50 0.863 / p95 1.240 / max 135.582 ms |
| Full source-to-replica exact verification | 3.372 / 3.474 / 3.428 s |
| Recovery-window whole-stack CPU | 240.360 core-seconds over approximately 189.382 s |
| Sampled aggregate memory mean / peak | 5.896 / 6.476 GiB |
| Recovery-window container disk read / write bytes | 7,745,536 / 887,115,776 |
| Network RX / TX, all roles including replication | 714,896,791 / 714,814,556 bytes |
| Post-recovery allocated data-volume blocks, all copies | 1,494,896,640 bytes |
| Orders-state ZSTD(3), uncompressed / compressed | 30,088,091 / 6,990,131 bytes; 4.304× |
| Raw-history ZSTD(3), uncompressed / compressed | 583,946,304 / 20,368,904 bytes; 28.669× |

Source/target bodies were compared as ordered streams, with the same SHA-256 on
all replicas; this is stronger than a row-count check. Native source fsync,
full-page writes and synchronous commit were on. Each target table reported
insert/directory fsync on; its user profile used quorum 2 with three active replicas.
The whole lab was capped at 7.5 CPUs / 16.625 GiB RAM on an 11-CPU / 24.416-GiB
Docker VM. Per-role CPU utilization, memory averages/peaks, native cgroup I/O
operation counts and image identities are in the report.

These are one-sweep diagnostics with synthetic, highly compressible data and
rate-limited actors. Docker's sampled I/O operation-count fields were unavailable;
post-run native `io.stat` counts have a different, current-incarnation scope.
Physical-device I/O latency/queue depth, transient peak disk allocation and
setup/seed phase averages were not measured. Source seeding preceded the resource
time series in this run; unrestarted-role native totals/high-water include it.
The current `make up` now wraps initialization with the same proven observer;
that added initialization wrapper was checked offline, not rerun over seeded data.
One ACK retained a truncated phase label from non-atomic metadata publication;
its body was correct and is reconciled, and the label remains a separate report
bucket. Phase publication now uses atomic rename. None of these gaps is filled
with invented zeroes or a throughput-winner claim.

`make report` refreshes the compact report after verified teardown; raw logs,
resource samples and witnesses remain ignored under `results/`.
