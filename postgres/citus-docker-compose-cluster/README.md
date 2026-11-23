# Citus — colocated tenant orders, recovery and shard moves

Citus with **three worker groups plus a coordinator group**, each with **one primary + two physical replicas**. The workload models tenant-local orders, dashboard reads and profile transactions. It keeps independent reads/writes active through primary failure, replica pause and **3 → 5 → 3** worker-group scaling.

## Quick start

1. `make docker-up` — build Citus/Patroni and start 12 PostgreSQL instances, three etcd members and HAProxy.
2. `make up` — identify the dataset, provision three colocated distributed tables and seed 10,000 customers.
3. `make test` — append 1,000,000 orders, verify retained ACKs and every physical worker shard, then run tenant dashboards/profile transactions.
4. `make down` — verify retained orders/profiles, then drop this example's tables.
5. `make docker-down` — remove only this project's containers, volumes, network and built image tags, including scale-profile workers.

Requires Docker Compose v2, Make and **Python 3.11+** (standard library only). Build tools, Go and PostgreSQL clients run in Docker. Allow at least 15 GiB Docker disk headroom; the expanded topology contains 18 PostgreSQL processes. SQL binds `127.0.0.1:5433`, overridable with `CITUS_PORT`. Credentials are local demonstration values. Compose project: `postgres-citus-orders`.

`orders-workload/` contains the Go writer, independent reader, historical/body verifier and tenant workload, with its own Dockerfile and pinned dependencies. No sibling example or generated state is required. Repeated setup and tests retain data; **no test truncates or drops the previous case's orders**. `down` is the explicit cleanup boundary. Keep ignored `results/`, including unsuccessful attempts. An example-local lock rejects concurrent controllers.

## Topology and settings

| Component / variable | Configuration |
|---|---|
| Citus | 14.2.0 source archive, SHA-256 checked, compiled natively |
| PostgreSQL | Runtime and development package both `18.7-1.pgdg12+2` |
| Patroni / etcd / router | 4.0.6 / 3.5.21, three DCS members / HAProxy 3.2.4 |
| Coordinator | Group 0, one primary + two physical standbys; HAProxy follows `/primary` |
| Workers | Groups 1–3 initially; add 4–5 during scale-out; three physical copies each |
| Placement | 30 logical shards **per table**; `customers`, `orders`, `customer_profiles` colocated by `customer_id` |
| Durability | Strict synchronous mode, one synchronous standby and synchronous commit; third copy asynchronous |
| Recovery | Checksums, logical WAL for moves, pg_rewind/rebuild, 512 MiB retained WAL per instance |
| Application | Go 1.25.1, `database/sql` + pgx 5.7.6, nonroot distroless runtime; Docker formatting/vet/unit gates |
| `COUNT` / `CUSTOMERS` | 1,000,000 new orders per finite case / 10,000 customers fixed at setup |
| `WORKERS` / `BATCH` | 8 writers / 100 orders for one customer per transaction |
| `CHAOS_RATE` | 25,000 offered rows/s during faults/moves; backpressure can reduce it |
| `MAX_ROWS` | `10 * COUNT` reserved IDs per live case; exhaustion before recovery fails |
| `FAULT_SHARD` | 1 by default; **worker/coordinator group**, not a logical shard ID; 0 targets the coordinator |
| `RUN_ID` | Unique timestamp/random suffix unless supplied; failed reservations cannot be reused |
| `ANALYTICS_QUERIES` / `ANALYTICS_REQUESTS` | 25 serial requests per query approach / 200 mixed tenant requests |

Thirty logical order shards are not thirty physical replicas. Citus owns placement/moves and colocated query planning; Patroni promotes PostgreSQL copies and updates Citus membership. The baseline has **12 PostgreSQL instances**: nine data-serving worker copies and three coordinator copies. Five worker groups make **18 instances**. A commit waits for the primary and **one** eligible synchronous standby, not all three. Retained WAL is not an archive or backup policy.

## Independent production tests

Run serially after setup:

| Command | Native operation and validation |
|---|---|
| `make failover FAULT_SHARD=1` | Discover and SIGKILL the actual group primary; prove exit 137, elect a different primary, require affected-group read/write progress while the old primary stays stopped, restore it and verify native rejoin. Repeat with groups 2 and 3. |
| `make failover-coordinator` | Same process-loss experiment on group 0; require HAProxy/client recovery and correct Citus metadata. |
| `make chaos-replica-lag FAULT_SHARD=1` | Discover and pause the current synchronous standby for 10 s under load, prove paused state, unpause and verify recovery. Groups 0–3 are separate cases. |
| `make scale-out` | Start owned groups 4–5 during live traffic, record their physical identities, run native background rebalance/wait and cleanup; require actual placement across all five workers. |
| `make scale-in` | Drain groups 4–5 under live traffic; finish native deferred cleanup, prove every removed physical copy has no shard tables, disable metadata sync while reachable, stop the group and remove its node entries. |
| `make test-production` | Baseline/dashboard; primary failure on each group 0–3; synchronous pause on each group; scale-out, expanded-topology dashboards, scale-in and full verification. At least **11 million new orders** by default. |
| `make benchmark` | Append another million orders without injection; keep the independent reader active. |
| `make tenant-analytics` | Compare global versus routed dashboards and run witnessed profile transactions against **all retained order history**. |
| `make verify` | Native roles, replication, placement, physical shard inventory and full order/profile body reconciliation. |
| `make test-placement-guard` | Negative control: misplace one witnessed order within its owner group, require physical verifier rejection, then restore its exact body and verify every copy. |
| `make unit-test` | Docker Go formatting/vet/oracle checks and Python controller/report guards. |
| `make status` / `make cli` | Owned services and native placement / coordinator SQL shell. |

The scale sequence is `scale-out` then `scale-in`. It never resets data. If a move fails partway, retain the expanded topology and diagnostics; stopping a worker that still owns data is not recovery. Repeated scale cycles reuse the recorded physical identities of their retained volumes.

## Observation and correctness

- A separate reader samples immutable acknowledged order bodies, one per known logical shard, or seeded customers on the first run. Probes run independently of the writer, every 100 ms with a 3 s deadline. Missing/wrong known bodies fail. This measures historical availability, **not fresh-write visibility lag**; serial probes and timeouts affect gap resolution.
- The writer journals and fsyncs each attempt/ACK/error outside the database containers. Each case reserves disjoint IDs, including failed cases. Retry uses the same composite keys with `ON CONFLICT DO NOTHING`; partial transactions fail. Live cases continue until native recovery and the minimum `COUNT` are both satisfied.
- Routing comes from Citus's native `get_shard_id_for_distribution_column`. Its logical shard IDs remain stable during rebalancing; owner groups can change. No client hash implementation decides ownership.
- Native recovery needs exactly one primary per active group, two expected streaming peers, a synchronous peer, matching physical system IDs and replay beyond frozen primary WAL flush positions. Citus must name the promoted primaries. Unrelated container/process identities must remain unchanged.
- Full verification reads customers and orders **separately** through the coordinator. Then it reads the assigned physical `*_shardid` tables on every worker primary and standby, tagging rows with their actual table's logical shard. It checks full bodies and placement **inside the correct group as well as across groups**; a JOIN or group total alone could hide misplaced rows.
- The physical inventory must exactly match native colocated placements, including profiles; leftover unassigned shard tables fail. Coordinator copies are checked for matching placement metadata. They are not counted as extra worker data copies.

## Witnessed tenant dashboards and profiles

The dashboard comparison requests the same tenant's answer either by fetching aggregates for every tenant or by putting the tenant key in SQL. Saved JSON plans must show **30 tasks versus one routed task**. Every result is checked against deterministic sums/counts folded from **all historical ACK journals**, not the last `COUNT`.

The mixed phase uses eight workers for 200 requests: **160 dashboard reads + 40 profile-upsert/read transactions**. Profiles live in their own colocated table; immutable customer names are never reset. Each analytics case reserves a monotonic profile version and journals/fsyncs mutation attempts and ACKs before/after the transaction. UPSERT cannot regress a newer version. The verifier folds the full profile history: no acknowledged version may regress/disappear, and an unacknowledged newer version must match an actual attempted body. These are mutable current profiles, not retained copies of every historical profile value.

Query timings are short, cache-affected observations with fixed execution order; no controlled cold-query profile or saturation claim. A genuine global dashboard still needs its aggregate. This comparison measures unnecessary fan-out when only one tenant's answer is needed. Profile transaction/query correctness is checked again after failure and rebalancing.

## Evidence and timing

`results/<dataset-UUID>/fixture.json` binds the native identity table to its external witness and initial group system identifiers. `groups/` records identities of later workers; `routing.json` holds native customer-to-order-shard mapping. Legacy tables without this identity are rejected instead of silently adopted/reset.

Each unique `runs/<RUN_ID>/` retains allocation, fsynced writes, independent reads, workload logs, writer statistics and success/failure proof. `checks/` keeps full native/physical verification. `analytics/<UUID>/` keeps version reservation, profile journal, plans, timings and full verification. Failed reports and ID reservations remain immutable.

Successful/failed request latencies are separate; successful batch completion also includes retries/backoff. Writer throughput includes witness fsync and excludes later full scans. Completion timestamps assign phases, while raw events keep request starts. Native-barrier timing starts after the restored container's health wait; markers and repair completion record the wider interval. Sampled read gaps are not exact outage durations. Fault traffic is paced and is not a capacity benchmark.

## Measured results

2026-11-16, Apple M4 Pro, native ARM64 Docker VM: **11 CPUs / 24.42 GiB**.
Every PostgreSQL copy: **1 CPU / 1 GiB**, etcd: **0.25 CPU / 256 MiB** each,
HAProxy: **0.5 CPU / 256 MiB**, each workload container: **2 CPUs / 1 GiB**.
Actual limits were inspected. CPU caps collectively exceed the VM's 11 CPUs;
this is one shared host, one run per case, not a saturation or cross-system ranking.
10,000 customers, eight writers, 100-row transactions. Fault/scaling traffic is
paced; baseline and final benchmark are unthrottled. Writer time excludes later
full-copy scans; successful batch latency includes retries/backoff.

| Case | New ACK orders | Writer s | Orders/s | Retries / ambiguous commit responses | Promotion / native operation s | Successful batch p50 / p99 / max ms |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 1,000,000 | 6.781 | 147,479 | 0 / 0 | — | 3.063 / 15.913 / 104.778 |
| Coordinator primary killed | 1,000,000 | 61.258 | 16,324 | 1342 / 1 | 21.450 | 1.907 / 12.349 / 21275.124 |
| Worker 1 primary killed | 1,000,000 | 61.580 | 16,239 | 1129 / 0 | 18.990 | 1.969 / 12.234 / 21493.912 |
| Worker 2 primary killed | 1,000,000 | 62.700 | 15,949 | 827 / 0 | 20.203 | 1.942 / 13.132 / 22639.499 |
| Worker 3 primary killed | 1,000,000 | 63.579 | 15,728 | 1142 / 0 | 20.099 | 1.893 / 13.301 / 23455.780 |
| Coordinator sync replica paused | 1,000,000 | 40.080 | 24,950 | 0 / 0 | 10 s hold | 1.981 / 11.400 / 105.755 |
| Worker 1 sync replica paused | 1,000,000 | 50.224 | 19,911 | 0 / 0 | 10 s hold | 1.974 / 14.154 / 10153.396 |
| Worker 2 sync replica paused | 1,000,000 | 50.192 | 19,924 | 0 / 0 | 10 s hold | 1.990 / 12.523 / 10157.759 |
| Worker 3 sync replica paused | 1,000,000 | 50.247 | 19,902 | 0 / 0 | 10 s hold | 2.018 / 10.526 / 10141.089 |
| Workers 3 → 5 | 3,093,300 | 126.658 | 24,422 | 0 / 0 | 107.527 | 2.028 / 37.729 / 496.985 |
| Workers 5 → 3 | 1,225,000 | 53.847 | 22,750 | 0 / 0 | 38.064 | 2.186 / 89.451 / 2046.857 |
| Final benchmark | 1,000,000 | 7.283 | 137,309 | 0 / 0 | — | 3.313 / 16.304 / 70.568 |

The native rebalance job reported **finished**. Order shard ownership changed
**10/10/10** across three workers to **6/6/6/6/6** across five; after drain it was
**10/9/11** across the original three groups. Each order shard retained its colocated
customer/profile shard. Scale-out verified **15 physical worker copies**; baseline
and scale-in verified nine, plus the coordinator's three metadata copies.
All **16 unrelated baseline services/process identities** survived each scaling
operation unchanged. Scale-in proved cleanup replay and empty/stopped state on
all six removed members before completing node removal.

The live generator exceeded its one-million minimum during both moves instead of
ending before native repair: **3,093,300 new ACKs** on scale-out and **1,225,000**
on scale-in. Each operation had **0 retries, ambiguous commits or read errors**.

| Case | Successful / failed reads | Successful read p50 / p99 ms | Failed read p99 ms | Longest per-shard successful-read completion gap s |
|---|---:|---:|---:|---:|
| Baseline | 255 / 0 | 0.626 / 7.281 | — | 3.102 |
| Coordinator primary killed | 596 / 174 | 0.492 / 6.255 | 79.259 | 24.926 |
| Worker 1 primary killed | 745 / 66 | 0.561 / 3.033 | 25.245 | 25.165 |
| Worker 2 primary killed | 764 / 70 | 0.554 / 4.487 | 24.929 | 25.297 |
| Worker 3 primary killed | 780 / 73 | 0.557 / 5.802 | 26.843 | 28.395 |
| Coordinator sync replica paused | 669 / 0 | 0.498 / 2.343 | — | 3.107 |
| Worker 1 sync replica paused | 756 / 0 | 0.529 / 3.111 | — | 3.163 |
| Worker 2 sync replica paused | 768 / 0 | 0.546 / 3.306 | — | 3.168 |
| Worker 3 sync replica paused | 805 / 0 | 0.572 / 3.166 | — | 3.157 |
| Workers 3 → 5 | 1682 / 0 | 0.462 / 1.110 | — | 3.315 |
| Workers 5 → 3 | 897 / 0 | 0.558 / 2.271 | — | 3.467 |
| Final benchmark | 463 / 0 | 0.634 / 2.732 | — | 3.101 |

All read probes returned **0 missing/wrong known bodies**. Gaps include the
30-shard probe rotation and query deadlines; they are not exact outage durations.
Final full reconciliation: **14,318,300 cumulative ACK orders across 12 histories**,
**120 acknowledged profile mutations across three versions**, and **40 current
profiles**. **0 missing ACKs, wrong values, misplaced rows, unknown rows, duplicate
IDs, partial batches or unresolved unacknowledged commits**. Every active physical
copy replayed and matched its native ownership/body expectations.

### Tenant query approaches

Twenty-five serial queries per approach; 200 mixed requests (160 reads + 40
witnessed profile transactions). Task counts were **30 global versus one routed**
on both topologies. The mixed timings include profile-witness fsync. Query p99 uses
nearest-rank indexing; with 25 samples p99 equals the maximum. Legacy 25-query
JSON reports used floor indexing: the table corrects their p99 exactly from the
recorded maximum. New reports also retain every latency sample. No controlled cold-cache run
or capacity claim.

| Dataset / workers | Approach | Requests | Operations/s | p50 / p99 / max ms |
|---|---|---:|---:|---:|
| 1,000,000 orders / 3 workers | all_tenants_then_select | 25 | 16.50 | 60.245 / 81.419 / 81.419 |
| 1,000,000 orders / 3 workers | mixed_tenant_transactions | 200 | 3,900.43 | 0.406 / 25.549 / 27.443 |
| 1,000,000 orders / 3 workers | tenant_filter | 25 | 3,272.89 | 0.276 / 0.707 / 0.707 |
| 12,093,300 orders / 5 workers | all_tenants_then_select | 25 | 3.15 | 306.227 / 387.737 / 387.737 |
| 12,093,300 orders / 5 workers | mixed_tenant_transactions | 200 | 2,463.40 | 0.982 / 21.685 / 24.778 |
| 12,093,300 orders / 5 workers | tenant_filter | 25 | 1,617.84 | 0.514 / 2.552 / 2.552 |
| 14,318,300 orders / 3 workers | all_tenants_then_select | 25 | 1.30 | 790.540 / 804.760 / 804.760 |
| 14,318,300 orders / 3 workers | mixed_tenant_transactions | 200 | 2,906.74 | 1.486 / 14.046 / 17.912 |
| 14,318,300 orders / 3 workers | tenant_filter | 25 | 250.89 | 1.327 / 68.249 / 68.249 |

The final tenant-filtered p50 was **1.327 ms** versus **790.540 ms** for fetching
all tenant aggregates. These answer the same single-tenant request with different
amounts of work; a true global request still needs the global aggregate.

### Placement negative control and exact recovery

`make test-placement-guard` deliberately moved one already acknowledged order into
the **wrong logical shard on the correct worker group**, preserving its ID/body
and total count. The physical verifier rejected it with **exactly 1 wrong-shard
row**, **0 missing ACKs, corrupt bodies, duplicates, partial batches or unknown
rows**. The fsynced row backup and failed `check.json` remain under
`checks/placement-negative-1794812514356637000/`. Idempotent repair moved only that
exact body back to its canonical physical table; the coordinator/all-copy verifier
again reconciled all **14,318,300** ACKs and profiles. No replay, truncation or reset
was used. This is deliberate placement corruption, not a disk-failure claim.

Repeated `make up` then preserved the whole dataset and all profile versions.
Go race/oracle checks, Docker formatting/vet/tests and four Python guard/report
tests passed. Failed initial formatting/catalog-audit attempts were diagnosed
without resetting their fixture; the catalog visibility setting is explained below.

| Case | Retained run ID in dataset `9ed8d49a-1b84-42aa-a5bc-f3d6a824bc5b` |
|---|---|
| Baseline | `20261116T063239-303b253d` |
| Coordinator primary killed | `20261116T063405-60ba4f7a` |
| Worker 1 primary killed | `20261116T063549-0f0a8bb4` |
| Worker 2 primary killed | `20261116T063735-e29a94e2` |
| Worker 3 primary killed | `20261116T063926-c08d777c` |
| Coordinator sync replica paused | `20261116T064119-fce29ade` |
| Worker 1 sync replica paused | `20261116T064255-e9f58a71` |
| Worker 2 sync replica paused | `20261116T064441-9019cc87` |
| Worker 3 sync replica paused | `20261116T064630-e8091148` |
| Workers 3 → 5 | `20261116T064824-6b518234` |
| Workers 5 → 3 | `20261116T065345-ae37b3b7` |
| Final benchmark | `20261116T065637-dcb69230` |

The final-source five-command lifecycle passed from **fresh volumes** with
another **1,000,000 ACK orders + 40 witnessed profile transactions**, all worker
copies/placements correct and **0 retries, ambiguous commits or data defects**.
Writer time was **6.614 s (151,184 orders/s)**. Evidence:
`results/fc8851fc-b2e1-438e-b530-f4c5c26001c1/runs/20261116T071935-c3c8f60d/`.
All raw query samples were retained and checked: **25/25 per serial approach**,
**200/200 mixed**, with nearest-rank p99 equal to max for the 25-query phases.
Both the earlier fresh lifecycle and this final statistics-corrected lifecycle
passed. Every dataset was verified before explicit cleanup. Final inspection found
**0 owned containers, volumes, networks or built image tags** remaining.

## Gotchas and remaining limits

- Physical shards are hidden from `pg_class` by default in this pinned Citus release. The first inventory attempt therefore saw zero tables despite successful direct physical reads. Audit/drain queries now use the **session-only** `citus.show_shards_for_app_name_prefixes='psql'` setting; hiding tables must never count as proof that a worker is empty.
- Runtime/development package mismatch previously produced `undefined symbol: heap_datum_check`; both packages are pinned identically before compilation.
- Patroni can rebuild a divergent old primary; 512 MiB WAL retention does not promise indefinite rewind or independent recovery after correlated storage loss.
- Deferred shard cleanup must finish before removing a group. Earlier reset-based tests hit `orphaned shard ... leftover from the previous move could not be cleaned up`; the cleaner skips removed groups. Disable metadata synchronization while the worker remains reachable, then stop/remove it.
- Every local copy shares one Docker host/disk. Process failure, pause and shard moves do not qualify physical disk loss, peer partitions, DCS/quorum loss, stale-primary rejection, independent backup/restore, rolling upgrades or bounded pressure. Those remain separate cases.
- Single-record throughput (`BATCH=1`), two-million minimum cases, hot tenants and controlled cold/warm query comparisons are not qualified by this run. Report them separately when added. Prior reset-based, JOIN-only, uncapped results are superseded by this qualification.

## Sources

- [Citus 14 utility functions](https://learn.microsoft.com/en-us/postgresql/citus/api-udf?view=citus-14): distribution lookup, rebalance, drain and node removal.
- [Pinned shard transfer](https://github.com/citusdata/citus/blob/v14.2.0/src/backend/distributed/operations/shard_transfer.c) and [deferred cleanup](https://github.com/citusdata/citus/blob/v14.2.0/src/backend/distributed/operations/shard_cleaner.c).
- [Pinned shard-visibility configuration](https://github.com/citusdata/citus/blob/v14.2.0/src/backend/distributed/shared_library_init.c).
- [Patroni 4.0.6 Citus integration](https://github.com/patroni/patroni/blob/v4.0.6/patroni/postgresql/mpp/citus.py).
- [Citus router planner](https://github.com/citusdata/citus/blob/v14.2.0/src/backend/distributed/planner/multi_router_planner.c).

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
