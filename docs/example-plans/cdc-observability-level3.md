# CDC and observability execution batch

Requested 2026-11-27; performance depth **3**, selected explicitly. All labs
are local Docker/Compose, with no real AWS credentials or resources. Commit
completed examples separately to main. No level-4/5 expansion.

## Ordered scope

- [x] PostgreSQL → Debezium/Kafka → ClickHouse: mutate during initial snapshot,
  observe snapshot-to-WAL transition, interrupt Connect with its offsets/slot
  retained, keep source writer/readers active, reconcile complete rows and ACKs.
- [x] MySQL → Debezium/Kafka → Flink → ClickHouse + Airflow: corrected margins,
  connector lifecycle, incremental backfill, schema evolution and reconciliation
  DAGs; exercise worker/task recovery and preserve all failed attempts.
- [x] Focused single-node GreptimeDB versus VictoriaMetrics/Logs: matched budgets,
  OTLP/native correctness, batch screen, three paced fresh-state trials, first/warm
  exact queries and live crash/witness recovery. Buffered and synchronous durability
  differ. Comparable clustered/HA profiles remain a later pass, not a claimed result.
- [ ] GreptimeDB versus Victoria versus Prometheus/Loki/Tempo: identical Go/Rust
  instrumented application, collector and request/span/log/sample corpus. Compare
  common query/correctness subsets; distinguish scrape gaps and push admission.
- [ ] ClickStack versus OpenObserve versus SigNoz versus Grafana: reuse that
  versioned incident corpus, with independent example-owned lifecycles and whole
  stack accounting. Compare ingestion/query data paths and operator investigation
  outcomes separately; include all hidden dependencies and edition boundaries.

## Bounded level-3 method

Survey supported stable releases/source before implementing. Establish healthy
correctness before faults or performance. Keep each project's README, Makefile,
source, deploy/config, scripts/tests, docs and curated reports together. New
examples own separate Compose names/networks/volumes and five lifecycle commands.

CDC fixtures default to a million orders, 10,000 customers and variable live
mutation counts; exact bodies, deletes, versions and transaction receipts must
match independent source/oracle readback. Native offset/slot/checkpoint evidence
must establish the actual handoff, fault and recovery, not merely row counts.

Observability runs use identical input/queries and declared total/per-role budgets
including collectors, queues, metadata and storage. Start with one common ingest
path and one native path; use batches 1/100/1,000/10,000 as a bounded initial
screen, then select representative cells for three alternating measured trials.
Do not cross-product every knob. Keep new-connection, engine-cold and physical
OS-cache-cold scopes distinct; unavailable controls remain unrun, never mislabeled.
Measure CPU utilization/core-seconds, average/peak memory, compression, logical
and replicated disk space, disk read/write bytes/IOPS and network at each phase.
Retain operation timing, visibility, errors/ambiguity, complete body oracles and
post-recovery ACK reconciliation. Unsupported signal/query/durability capabilities
are separate outcomes, not artificial zero scores.

Run heavyweight comparisons serially. Independent correctness/build work can
overlap only within measured CPU/RAM/disk headroom. Do not reopen completed
Flink/Doris/storage deployment examples for their optional deeper matrices.

## Execution evidence

[PostgreSQL handoff](../../pipelines/postgres-cdc-clickhouse/) completed its
corrected five-command lifecycle: 1,310 live source ACKs, 119 additional ACKs
through the held connector outage, and exact 1,000,861-row readbacks on each of
three replicas. Task restart took 58.205 seconds. The initial failed reader,
missing heartbeat topic and verified retained-state recovery remain documented.
Resource measurements and their explicit setup/peak-disk gaps are in the curated
report.

[MySQL current margins + Airflow](../../pipelines/mysql-flink-clickhouse-airflow/)
completed the corrected required case set and verified teardown: 5,953 ACKs
including failed attempts reconciled, 1,005,325 current rows matched on all three
copies, no repeated initial snapshot, native backfill/reconciliation DAGs succeeded.
Connect kill-to-recovery was 77.173 s and TaskManager recovery 32.101 s. Six failed
case statuses remain in the curated evidence; infrastructure/SQL submission and
controller mistakes are documented separately from backend behavior. Corrected
cases ran independently after the initial `make test` failures; no reseeding hid
those attempts. Nine offline controller controls and three Go checks passed.

The [matched metrics/log lab](../../observability/greptime-victoria/single-node/)
completed the selected level-3 single-node pass: common/native million-record
controls, four batch sizes, three fresh-state paced trials, exact first/warm queries
and live SIGKILL/witness recovery. Twelve Go checks and three cleanup guards pass.
All 9,971,000 Greptime and 10,168,000 Victoria writer ACKs across warmup/measurement
reconciled; all 558 analytical queries matched their independent oracle. Victoria
used lower CPU/memory and returned these selected queries faster. Its buffered
profile lost acknowledged data under crash, while Greptime retained synchronous-WAL
ACKs; there is no equal-durability overall winner. Failed verifier expectations,
the first failed crash status and explicit external replay remain documented.
[Comparison numbers and limits](../../observability/greptime-victoria/single-node/docs/COMPARISON.md).

Three Greptime crash cases retained all 37,000 ACKs each. VictoriaMetrics needed
replay for 47,000 and 48,000 missing metrics; VictoriaLogs needed replay for
29,000 logs. All original ACK bodies matched after repair.
[Recovery runbook](../../observability/greptime-victoria/single-node/docs/RECOVERY.md).
The [three-signal incident lab](../../observability/three-signal-incident/) has
passed full healthy Greptime/Grafana native readback: 1M metrics, 1M logs and
30k spans each. Victoria's experimental Tempo API changes numeric attribute
types to strings; its original partial corpus reconciled all 581k metric, 558k log
and 17,460 span ACKs with that explicitly failed type contract. The corrected full
Victoria case reconciled 1M metrics, 1M logs and 30k spans with the same failed
type projection. The retained restart audit then found 44,274 missing Loki log
ACKs in two full reads; physical loss versus index/read-path failure remains
unisolated. Grafana collector injection is unrun; Greptime/Victoria collector
cases have run: both retain every ACK after a ten-second collector outage;
Victoria's typed trace contract remains failed. All ten owned services are now
stopped without deleting failed data. The failed data and native logs remain preserved. The first reader
incorrectly abandoned samples before Victoria's 20-second index flush; its failed
run remains preserved. Broader incident-stack deployments remain unrun. Comparable
clustered/HA and further storage qualification remain a later pass.

Current upstream deployment research found that SigNoz now uses Foundry (pinned
research release 0.5.0); its old repository-bundled Compose deployment is deprecated.
The supported topology includes PostgreSQL metadata as well as ClickHouse, Keeper,
collector and SigNoz, all of which must count against its budget. ClickStack 2.47.0
ships a combined app/API, MongoDB, ClickHouse and collector; inspect its older
MongoDB pin/support before choosing the lab dependency. OpenObserve's local
single-node storage is separate from its NATS/PostgreSQL/object-store HA topology.
No upstream installer was executed and no real AWS configuration was inspected.
Sources: [SigNoz supported Docker workflow](https://signoz.io/docs/install/docker/),
[ClickStack 2.47 compose source](https://github.com/ClickHouse/ClickStack/blob/%40hyperdx/app%402.47.0/docker-compose.yml),
[OpenObserve deployment modes](https://openobserve.ai/docs/architecture/).

## Implemented pipeline architecture

The MySQL margin pipeline uses pinned supported artifacts: MySQL
9.7.4.1, Debezium 3.7.1.Final, Kafka 4.3.2, Flink 2.3.0 and Airflow 3.4.0.
Its README/report record effective versions, digests and the combined budget.

- Source orders carry integer revenue/cost, revisions and cancellations; customer
  changes must update existing joined margins. Flink maintains a current-table
  join and emits a changelog to Kafka; replicated ClickHouse resolves versions
  and deletion tombstones. Preserve before/after semantics rather than treating
  revisions as independent sales. Verify read-committed behavior before claiming
  checkpoint/transaction isolation on the output topic.
- Add a shipping-cost column through source DDL, with explicitly designed nullable
  compatibility in the Flink projection and a zero default for old envelopes.
  Update revisions, verify schema-change events and reconcile the resulting
  margins. This qualifies a planned additive evolution, not arbitrary automatic
  schema discovery. Do not change primary keys during incremental snapshots.
- Airflow requests a native Debezium incremental snapshot using a stable signal
  identity, waits for progress and runs the independent exact reconciliation DAG.
  Keep writes live; interrupt/recover a connector/task without deleting offsets,
  schema history, checkpoints or the signal. A retried orchestration task must
  reuse its logical request identity and verify the existing result.

Sources: [MySQL current patch](https://dev.mysql.com/doc/relnotes/mysql/9.7/en/news-9-7-4-1.html),
[Debezium incremental snapshots and schema boundaries](https://debezium.io/documentation/reference/3.7/connectors/mysql.html#mysql-incremental-snapshots),
[Flink changelog JSON](https://nightlies.apache.org/flink/flink-docs-release-2.3/docs/connectors/table/formats/debezium/),
[Airflow 3.4 architecture](https://airflow.apache.org/docs/apache-airflow/3.4.0/core-concepts/overview.html).
