# MySQL CDC → Flink current margins → ClickHouse + Airflow

A local current-state CDC join with live mutations, planned additive schema change,
Airflow backfill/reconciliation and retained-state recovery. Performance depth **3**:
focused correctness/recovery and resource diagnostics, with no capacity ranking.
It owns all source, state, services and its isolated internal network.

## Quick start

1. `make docker-up` — build/check pinned actors and prepare the owned infrastructure.
2. `make up` — seed a million orders, register native CDC and submit the Flink join.
3. `make test` — verify margins, transaction isolation, lifecycle, schema/backfill and recovery.
4. `make down` — reconcile the saved acknowledged history and remove application state.
5. `make docker-down` — remove the owned containers, volumes, network and built images.

Prerequisites: Docker Compose, Python 3.13 (the tested controller version), and
a Docker VM with at least the recorded memory budget. `ORDER_COUNT` defaults to
one million (supported controller range one to two million; only the default
was measured); `CUSTOMER_COUNT` must remain 10,000 for the historical oracle.

No real AWS resources/configuration, hosted accounts or external runtime endpoints
are involved. All credentials are disposable local values; no host ports are exposed.

## Architecture and contract

One MySQL primary → Debezium → Kafka RF3/minISR2 with three separate KRaft
controllers → two Flink TaskManagers → keyed, transactional Kafka changelog →
one ClickHouse shard with three replicas and three Keeper voters. Airflow's API,
scheduler/LocalExecutor, DAG processor and PostgreSQL metadata database are included
in the budget. They are orchestration dependencies, not extra analytics engines.

Orders use integer revenue, cost, shipping and discount basis points. A changing
customer updates existing joined margins. Cancellation and hard source deletion
produce retractions. This is a current-state join, not an event-time/as-of join.
The planned shipping column exists as a nullable field in Flink before source DDL;
old envelopes use zero. This does not promise arbitrary automatic schema evolution.

Flink emits update-before/update-after as delete/insert envelopes. Target replacement
uses Kafka offsets, with an explicit invariant that each order remains in one of
four fixed partitions. Source business revisions alone cannot order a same-revision
delete/insert pair. Increasing partition count or changing key encoding requires
a new ordering/migration design; it is not a supported live configuration change.
Read-committed ingestion must pass an explicit held/aborted transaction control.
Committed per-partition barrier records fence the output; transaction control
markers mean that record count is not equal to end offset.

Airflow requests incremental snapshots using a stable logical signal identity and
observes native Debezium completion notifications. A separate reconciliation DAG
compares every output field with independent source SQL after mutations are fenced.
A DAG retries the same signal identity instead of creating another backfill.

## Operational commands and evidence

`make connector-lifecycle`, `make backfill-recovery` and `make worker-recovery`
run independently against retained initialized state. `make test-production` runs
them serially after the transaction-visibility control. `make verify` reconciles
existing data without reseeding. `make report` refuses to publish a passing
qualification unless the latest required case of every type passed.

The [recovery guide](docs/RECOVERY.md) records the exact case boundaries, controller
failures, corrections and unrun future faults. Failed statuses remain failed even
when their native recovery succeeded and a corrected case later passed.

## Measured results — 2026-11-28

The four required cases passed on the retained fixture after controller fixes.
The initial `make test` attempts failed; corrected backfill and worker targets
were run separately rather than hiding those attempts in a fresh fixture.
[Machine-readable evidence](reports/QUALIFICATION.json) includes all six failed
case statuses, source manifests, operation timings, native checks and resource
windows. Infrastructure/submission failures are also explained in the recovery guide.

Input: **1,000,000 orders / 10,000 customers**. Final reconciliation covered
**5,953 acknowledged transactions**, including earlier failed attempts, with
**0 missing/wrong ACK effects**. All eleven output fields and EOF matched source
SQL on each of **three replicas**: **1,005,325 active rows**, identical SHA-256
`9978b8840a78c9cef36ec027858c58cdd58454fdb42fd47eacb7bb0e2e2f30c4`.
The current row count differs from the seed because live transactions insert,
cancel and delete orders. Native replication was writable with all three copies.

| Focused case | Observed outcome |
|---|---|
| Transaction visibility | Held uncommitted data stayed invisible; aborted data stayed absent; committed control visible on all three copies |
| Connector pause/resume | 10.062 s hold; 96 additional source ACKs; running + newly sampled ACK visible in 5.099 s after resume |
| Interrupted incremental backfill | 10.410 s held crash; 98 additional source ACKs; kill → running + newly sampled ACK visible in 77.173 s; native Airflow snapshot DAG succeeded |
| No initial resnapshot | Initial-read coordinates stayed **1,000,000 → 1,000,000** through incremental recovery |
| TaskManager crash | 10.230 s hold; 98 additional source ACKs; kill → recovered vertices/new checkpoint/newly visible ACK in 32.101 s |
| Reconciliation DAGs | Native Airflow tasks succeeded after each corrected recovery case |

Source transaction p50/p99 during connector pause: **1.788/5.072 ms** (146
samples); during Connect crash/restart: **1.721/18.600 ms** (737); during worker
loss/recovery: **1.579/8.183 ms** (308). These are one-sweep diagnostic estimates,
not stable tail percentiles. Full million-row comparisons took **5.236–5.392 s**
per fixed replica at the final cut. The writer deliberately waits 100 ms between
transactions; neither its achieved rate nor these fault timings establish capacity.

Environment: Docker 29.5.3, Linux 6.12.76, ARM64, **11 VM CPUs / 24.416 GiB RAM**.
Pinned images, actual limits and client settings are in the report/contract.
Only this lab was running; all roles share one physical Docker VM. Resource
windows include preflight/final checks and, for the original test invocation,
a failed backfill attempt. They are **not isolated per-case cost comparisons**.

| Observed invocation | CPU core-s | Mean / peak RAM (GiB) | Disk read / write bytes | Network RX / TX bytes |
|---|---:|---:|---:|---:|
| Original test: protocol, connector, failed backfill | 526.328 | 12.991 / 14.245 | 11,665,408 / 2,705,547,264 | 1,192,124,141 / 1,192,123,706 |
| Corrected backfill target | 465.889 | 14.733 / 15.228 | 41,181,184 / 2,540,503,040 | 1,333,014,710 / 1,332,953,835 |
| Worker target | 249.280 | 14.162 / 15.075 | 11,120,640 / 783,732,736 | 653,408,602 / 653,173,947 |

Per-role average/min/max CPU utilization, memory and reset-aware counters are in
the report. Final allocated named-volume data: **4,978,896,896 bytes**
(**4.637 GiB**), including replicas, WAL/binlog,
RocksDB/checkpoints and Airflow metadata/log volumes. This excludes image/overlay
layers, Docker-engine log storage and raw host evidence. Snapshots are not a
continuous transient-space peak, and merge progress differs between snapshots.

On one fixed copy, history ZSTD: **2,727,865,106 → 150,991,980 bytes
(18.066×)**; typed margin columns with default LZ4:
**95,675,323 → 33,147,640 bytes (2.886×)**.
Other replicas and active-part allocation are recorded separately. Background
merges retain more physical versions than the logical current row count; no
forced `OPTIMIZE FINAL` was used to manufacture a storage number.

Physical device latency/queue depth and guaranteed physical disk IOPS are
unavailable here. Native `io.stat` is retained, and Docker sampled byte counters
are reported; neither is a device-level durability proof. Sparse tails and sampled
first-visibility ages remain diagnostics. Setup/seed and short-lived tool cgroups
are separate raw evidence; the table covers the selected test windows only.

## Files

- `orders-workload/`: native Go MySQL producer, exact margin oracle, transaction
  probe/fence and backfill commands, plus its Dockerfile and dependency lockfiles.
- `dags/`: Airflow orchestration; Go performs the application data checks.
- `sql/`, `config/`, `compose.yaml`: native source, join, capture and deployment.
- `docker/`: pinned Flink connector and Airflow images; no custom Java application.
- `scripts/`, `tests/`: lifecycle/fault controllers and independent offline controls.
- `reports/`, `docs/`: curated good/bad results and runbooks; raw `results/` and
  materialized/build caches are ignored.

Single-host Docker and local checkpoint storage cannot establish independent-host
HA or power-loss durability. MySQL, the JobManager, checkpoint volume, Airflow
metadata primary and Connect worker are explicit single failure points. Production
security, source-primary failover and arbitrary schema changes remain separate work.

Schema rationale: per `schema-pk-prioritize-filters`, the current-state order ID
is the sorting/replacement identity. Per `schema-types-lowcardinality`, region
and topic use dictionary-encoded strings. The full current join is maintained in
Flink so dimension changes propagate. Per the upsert architecture
guidance, the target is a derived current table over retained change history,
with explicit offset ordering and logical tombstones. These are workload-derived
choices, not a universal ClickHouse schema.

Sources: [Debezium MySQL 3.7](https://debezium.io/documentation/reference/3.7/connectors/mysql.html),
[native notifications](https://debezium.io/documentation/reference/3.7/configuration/notification.html),
[Flink Debezium encoding](https://nightlies.apache.org/flink/flink-docs-release-2.3/docs/connectors/table/formats/debezium/),
[Kafka keys and transactions](https://nightlies.apache.org/flink/flink-docs-release-2.3/docs/connectors/table/kafka/),
[ClickHouse Kafka configuration](https://clickhouse.com/docs/engines/table-engines/integrations/kafka),
[Airflow 3.4 architecture](https://airflow.apache.org/docs/apache-airflow/3.4.0/core-concepts/overview.html).
