# Order CDC joins into replicated Doris

PostgreSQL → Debezium/Kafka → Flink current-table customer join → two Doris tables.
The same input reaches a checkpoint/2PC sink and an asynchronous batch sink. This
is a pipeline project with its own deployment/state; existing Flink and Doris
system examples are already complete at level 3 and are not prerequisites.

## Quick start

1. `make docker-up` — build/start the owned PostgreSQL, Kafka, Flink and Doris roles.
2. `make up` — create tables/topics, register CDC and start the native streaming join.
3. `make test` — seed 1,000,000 orders/10,000 customers and verify customer corrections.
4. `make down` — verify all retained ACKs, cancel processing and remove the Doris database.
5. `make docker-down` — remove only this project's containers, volumes, network and local image tags.

Requires native ARM64 Docker with 11 CPUs, approximately 24.4 GiB RAM, at least
8 GiB free disk **after image builds**, Compose and Python 3.9+. The controller
checks all declared caps, including both TaskManagers and the temporary SQL client,
with 2 GiB reserved headroom. No real cloud configuration/resources, AWS SDK,
public service endpoints or published host ports are used. Stop other heavy labs.

`ORDER_COUNT=1000000 CUSTOMER_COUNT=10000` configure the fixture. Smaller smoke
fixtures need at least 10,000 orders and 100 customers. Do not delete journals or
reseed an existing fixture after failure; use `make verify` and diagnose it.

## Layout

- `orders-workload/`: Go, native maintained PostgreSQL/MySQL drivers, source
  mutation receipts, durable external witness, exact independent join reader and
  standard-library compression/resource measurements. Go follows the established
  SQL-driver workload in the Doris examples; there is no custom Java application.
- `sql/`: native Flink SQL source PKs, CDC normalization, stateful join and two sinks.
- `config/`: PostgreSQL schema/roles, Debezium and declared correctness contract.
- `docker/`, `docker-compose.yml`: pinned native images, example-owned roles/state.
- `scripts/`: existing-style standard-library Python lifecycle, native API/replica
  checks, fault orchestration and resource collection; no repository-wide harness.
- `reports/`: curated good/bad results once executed. Ignored `results/` preserves
  histories, original logs and generated input; `.build/` holds disposable artifacts.

## Contract and boundaries

Source data has integer money, 10,000 customers across 16 regions, monotonic order
and customer revisions, cancellations and an integer business timestamp. Corrections
can have an older business timestamp while carrying a newer revision. This is a
current-table join, not an event-time window or temporal/as-of join. Customer
updates must also change already-materialized orders. No join-state TTL silently
drops old customers or orders. Flink 2.3 requires an explicit sink conflict policy
for this inferred key shape: `ON CONFLICT DO DEDUPLICATE` retains retraction history.
This is state intensive and needs a separate long-term retention design; disabling
the planner’s conflict check or dropping conflicting updates would change semantics.

PostgreSQL commits with fsync/full-page writes/synchronous commit verified on.
Every mutation batch writes a unique receipt in the same transaction, surrounded
by fsynced external intent/ACK records. A failed commit is ambiguous; it is not
blindly retried. Readback reconciles all intents, ACKs and transactional receipts.
ACKs count **batches**, not per-row independently acknowledged writes.

Debezium may replay changes. PostgreSQL tables use `REPLICA IDENTITY FULL` and
Flink enables PK-based CDC changelog normalization. Each Doris table uses Unique
Key merge-on-write, RF3 and a sequence value `order_revision*1000000+customer_revision`.
Source constraints bound revisions so the calculation fits BIGINT. Deletes and
cancellation-filter retractions must remove rows, not leave stale results.

The two sinks have different guarantees: checkpointed writes use the native Doris
connector's 2PC; asynchronous batches flush by size/time and rely on idempotent
keys/sequences. Final snapshots alone do not prove end-to-end exactly-once or
linearizability. Exact ordered readback compares all eight fields against a
quiescent PostgreSQL snapshot, not just counts or aggregates.

Kafka has three separate controllers and three brokers, RF3/minISR2. Doris has
three voting FEs and three BEs; native membership/tablet versions must confirm
three distinct replicas. Flink uses one JobManager and two TaskManagers with
RocksDB state and an owned shared checkpoint volume. PostgreSQL, that volume,
the JobManager and the single Connect worker are explicit single failure points.
All containers share one physical host; this is not independent-host durability.

Native memory/CPU settings are deliberately small local-lab budgets, not production
sizing: FE heaps 512 MiB, BE native limits 2.25 GiB inside 2.75-GiB containers, Kafka heaps 128/256 MiB. BE jemalloc uses two arenas and a 4-KiB thread-cache maximum;
these are constrained-lab overrides, not production defaults. Resource
limits do not establish actual resource use; executed reports must include every
role and effective settings. PostgreSQL writer/CDC and Doris connector identities
have separate scoped roles. Admin/readback controls remain lab-only, plaintext
and isolated; security qualification is outside this fixture.

## Separate tests

| Command | Scope |
|---|---|
| `make verify` | Reconcile existing source witnesses and both full sink snapshots |
| `make worker-failure` | Keep source writer/historical reader live; prove a TaskManager SIGKILL, hold ten seconds, restore and verify native checkpoint recovery |
| `make dimension-test` | Propagate customer corrections to already-materialized orders |
| `make verify-routing` | Exact point predicates, including absent IDs, through each fixed replica |
| `make delete-test` | Hard-delete and cancel separate ranges; verify absence in both sinks |
| `make verify-replicas` | Full readback using each fixed replica index, without missing-version skips/fallback |
| `make test-production` | Serial verify → worker failure → deletion → three-copy readback |
| `make report` | Curate a completed current fixture before teardown; refuse missing/foreign/failed evidence |
| `make resources` | Owned-volume sizes and native cgroup CPU/memory/disk-byte/I/O counters |
| `python3 scripts/observe.py test` | Sample all owned roles during the selected Make target |

Run fault tests after the initial baseline. Each live-fault invocation has its own
case ID and preserved source journal. It never pauses the producer or resets data.
Historical live reads deliberately cover immutable older IDs while corrections
continue; final all-row checks cover the mutated IDs and dimensional changes.
A worker crash near a checkpoint does not prove interruption at an internal Doris
commit instruction; capture the actual checkpoint/transaction observations.

## Setup and recovery findings

The first million-order test failed; it is retained as a failed result. Port
health checks passed while the native BE rejected even small allocations. Raising
its 1.5-GiB native limit to 2 GiB allowed setup but stream loads exhausted memory,
Flink exhausted its 20-restart budget, and its non-externalized checkpoints were
removed. Raising to 2.25 GiB alone still refused full-body reads. A native process
profile showed about 972 MiB of jemalloc cache (1,020,651,168 tcache bytes).
Reducing per-CPU arenas to two arenas and limiting the thread cache to 4 KiB
allowed exact readback; source and sink tables were never cleared.

Kafka retained offset zero on every source partition. `make replay` submitted the
same SQL with new sink transaction-label namespaces and replayed that complete log
into existing Doris tables. Full readback subsequently checked **1,000,000 rows in
each sink**, all eight fields, and **41 acknowledged transaction batches** with no
missing receipts. This is log-based projection recovery, **not checkpoint restore**.
The corrected SQL retains externalized checkpoints; shared local storage and the
single JobManager still are not a highly available checkpoint service. Never replay
a partial log or silently change sink state. `make resume-up` applies only to a
preserved initializing setup, not a ready or failed running job.

Other preserved setup failures:

- Dynamic Docker IP allocation collided with fixed FE addresses. Restricting
  dynamic addresses to `172.30.67.128/25` preserved all volumes; source tables
  were still empty. See [the IPAM report](reports/INITIAL_IPAM_FAILURE.json).
- Remote Docker `ADD` created connector JARs mode 0600. The non-root Flink process
  could not discover the Kafka factory. The build now sets mode 0644 and checks
  readability as that user. Native SQL-client errors are explicitly checked in
  addition to its process exit status.
- Flink 2.3 rejected the inferred upsert key until the explicit conflict policy
  was supplied. The fix retains deduplication history rather than disabling the
  check or dropping updates.
- Connect briefly returned 404 after accepting connector creation; only that
  explicitly expected status is retried. Other HTTP errors remain failures.
- Unprefixed replication settings were ignored by the Debezium image: native
  internal-topic RF was **1**, despite the requested value 3. Use `CONNECT_*`
  settings and verify native RF/ISR. `make repair-connect-topics` repairs existing
  internal topics in place, preserving offsets instead of recreating them.

## Results — 2026-11-27

[Curated qualification](reports/QUALIFICATION-2026-11-27.json) includes individual
trials, all-role resources, native replicas/configuration and source hashes.
[Failed setup and retained recovery](reports/SETUP-AND-RECOVERY.json) remains a
separate result. The original million-order baseline failed; later retained-data
recovery and the following correctness cases passed.

| Check | Measured outcome |
|---|---|
| Source corpus | 1,000,000 orders, 10,000 customers; 41 initial acknowledged transaction batches |
| Customer corrections | 100 customers changed; both sinks exactly matched all 1,000,000 current joined rows |
| TaskManager loss | SIGKILL/exit 137, ten-second hold, live producer unpaused; native restored/RUNNING at 17.525 s |
| Live traffic during fault | 200 successful 100-row correction batches; 188 exact historical queries, zero reader errors |
| Checkpoints across fault | 92 → 93 completed, 0 → 2 restorations, 1 → 4 failed checkpoints; this was not a failure-free checkpoint run |
| Delete/retraction | 1,000 hard deletes + 1,000 cancellations; exactly 998,000 active joined rows in each sink |
| Final ACK reconciliation | 244 committed batches; zero missing ACK receipts or unacknowledged committed batches |
| Replica readback | All 998,000 full bodies per sink through each of three fixed replicas; no skip/fallback |
| Point routing | 70 IDs × two sinks × three replicas = 420 checks, including deleted/cancelled absences |
| Topology | Three voting FEs, three BEs; eight tablets / 24 current replicas per sink; source and Connect topics RF3/minISR2/full ISR |

The query trials use a repeated **64-row historical working set**, one synchronous
worker, ten discarded warmup queries and 100 measured queries per table/trial.
Orders alternate across three fresh processes. These are **short hot-query latency
diagnostics**, not a competitive capacity benchmark or physically cold reads.
Both sinks share the same database resources; their ingestion guarantees differ.

| Sink | Mean latency per trial, ms | p50 range, ms | p95 range, ms | p99 range, ms |
|---|---|---|---|---|
| Checkpointed | 9.481 / 8.034 / 9.454 | 7.315–8.281 | 9.450–14.074 | 13.448–19.600 |
| Asynchronous | 7.884 / 8.897 / 7.506 | 7.122–8.303 | 9.590–12.229 | 11.280–14.786 |

| Resource scope | Measured outcome |
|---|---|
| Live worker-failure observation | 60.181 sampled core-seconds; 13,815,635,968-byte aggregate container-memory peak; 13,877,248 disk read bytes / 677,838,848 disk write bytes |
| Three query trials plus verification | 70.758 sampled core-seconds; 13,482,749,952-byte aggregate container-memory peak; 192,512 disk read bytes / 1,714,085,888 disk write bytes |
| Settled owned volumes | 1,037,729,792 allocated bytes / 6,389,254,605 apparent bytes; sparse Kafka files make these different |
| Native cgroup I/O snapshot | 253,579,264 read bytes / 35,450 reads; 10,016,972,800 write bytes / 153,000 writes; cumulative current-container lifetimes, not only query trials |
| Source CSV reference compression | 34,878,296 → 9,155,391 bytes, gzip level 1, 3.810×; 1,508.452 ms compression/generation |

Resource reports include all database, broker/controller, Connect, Flink and
workload roles, per-role mean/peak CPU and memory, network and effective caps.
Docker and native I/O windows differ; never add them together. The owned-volume
snapshot excludes image layers, container writable layers/logs and external witness
files. Native ZSTD/LZ4 compression ratios, device I/O latency/queue depth and a
physical peak-storage series remain unmeasured. The CSV reference ratio is not a
native storage-compression result. There is no performance winner.

The million-row replay job's effective checkpoint timeout/minimum pause were
600 seconds/0 seconds: cluster-level declarations did not override the SQL client's
job defaults. Final SQL sets 120 seconds/1 second explicitly; its fresh **10,000-order / 100-customer** lifecycle
smoke passed, including RF3/ISR, point routing and owned teardown. Both fixtures
were verified before their owned containers, volumes and network were removed. These differences remain visible in native
configuration evidence rather than being retrospectively applied to old trials.

## Limits and future work

This scope covers one live worker failure, not Kafka broker/controller loss,
PostgreSQL failover, JobManager HA, permanent checkpoint-disk loss, network
partitions or independent restore. Source-log replay requires complete retained
history; it is not a backup policy. Two-phase commit configuration and snapshot
agreement do not establish an end-to-end exactly-once theorem. Join/deduplication
state is unbounded here; production retention, rescaling and larger workloads need
a separate design. Matched ClickHouse comparison and historical Spark corrections
belong to subsequent projects, not claimed results of this lab.

## Sources

- [Retained checkpoint semantics](https://nightlies.apache.org/flink/flink-docs-release-2.3/docs/ops/state/checkpoints/).
- [Doris memory analysis](https://doris.apache.org/docs/4.x/admin-manual/trouble-shooting/memory-management/memory-analysis/jemalloc-memory-analysis/) and [allocator options](https://jemalloc.net/jemalloc.3.html).

- [Doris release selection](https://doris.apache.org/download/).
- [Flink 2.3 and Kafka connector compatibility](https://flink.apache.org/downloads/).
- [Pinned Doris connector compatibility](https://github.com/apache/doris-flink-connector/blob/26.3.0/README.md).
- [Sink guarantees/configuration](https://doris.apache.org/docs/4.x/connection-integration/data-integration/flink-doris-connector/).
- [Native prepare/restore](https://github.com/apache/doris-flink-connector/blob/26.3.0/flink-doris-connector/flink-doris-connector-base/src/main/java/org/apache/doris/flink/sink/writer/DorisWriter.java)
  and [commit handling](https://github.com/apache/doris-flink-connector/blob/26.3.0/flink-doris-connector/flink-doris-connector-base/src/main/java/org/apache/doris/flink/sink/committer/DorisCommitter.java).
- [CDC duplicate/full-row requirements](https://nightlies.apache.org/flink/flink-docs-release-2.3/docs/connectors/table/formats/debezium/).
- [Debezium 3.7.1 stable and PostgreSQL compatibility](https://debezium.io/releases/3.7/).
  PostgreSQL 18.7 is the supported stable line used here; the connector's published
  test matrix does not qualify PostgreSQL 19.
