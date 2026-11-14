# MySQL NDB Cluster: partitioned orders and failures

NDB Community 8.4.11 with three hash partitions, three copies per partition, two SQL frontends and two management nodes. The Go client sends customer-local transactions through HAProxy while a separate file records acknowledged IDs.

## Quick start

1. `make docker-up` — start and wait for all nine data nodes and both SQL frontends.
2. `make up` — create NDB tables, build the client image and seed 10,000 customers.
3. `make test` — reconcile 1,000,000 orders, exercise 1,000,000 subscriber profiles and check partition/replica configuration.
4. `make down` — drop the example tables.
5. `make docker-down` — remove this Compose project's services, volumes and locally built client image.

Requires Docker Compose v2, Make and sufficient Docker VM disk space. Use at least 8 GiB free for this example alone. The Oracle image is amd64; Apple Silicon runs it under emulation. Port `127.0.0.1:13306` is overridable with `NDB_PORT`. Credentials are local example values in Compose.

## Workload and architecture

| Component | Configuration |
|---|---|
| Data nodes | `data1`–`data9`, three explicit node groups, `NoOfReplicas=3` |
| Storage | Named volume per node; `DataMemory=256M`; 2 execution threads per node |
| SQL / management | `sql0`/`sql1`; `mgm0`/`mgm1`; Oracle Community Cluster 8.4.11 |
| Router | HAProxy 3.2.4, health-checked SQL frontends |
| Schema | NDBCLUSTER, `PARTITION BY KEY(customer_id) PARTITIONS 3` |
| Workload | `COUNT=1000000 CUSTOMERS=10000 WORKERS=8 BATCH=100 BULK=1` |

NDB SQL nodes share the distributed storage engine. They are not independent MySQL shards: NDB owns placement, replication and cross-partition execution. `check-cluster.sh` verifies three actual partitions, all nine data nodes started, `ndb_desc`'s three-copy configuration, and matching SQL totals through both frontends. This does not independently read each physical replica's bytes.

The client uses Go modules and `database/sql` with `go-sql-driver/mysql`. Each batch targets one customer. IDs and values are deterministic; ambiguous commits retry the same IDs using a unique key. Every successful batch is appended and fsynced to `results/<RUN_ID>/acknowledged.jsonl`. Verification checks every joined order against that journal, customer placement, amounts and duplicates. `missing-acknowledged.json` contains exact lost IDs if reconciliation fails. A failed or incomplete run exits nonzero.

## Separate failure tests

Run these after `make up`. Each creates a fresh journal, resets orders, and leaves producers running. Fault tests default to `CHAOS_RATE=25000` records/s as an offered-rate ceiling, not a guaranteed throughput.

| Command | Fault |
|---|---|
| `make failover` | SIGKILL `data1`, keep it down for 10 seconds, restart and wait for rejoin |
| `make failover FAULT_NODE=data4` | Same test in another node group |
| `make chaos-sql` | SIGKILL SQL frontend `sql0`; connections retry through HAProxy |
| `make chaos-management` | SIGKILL `mgm0`; use the second management node |
| `make status` | Compose health, NDB membership and data-node status |
| `make cli` | SQL shell through `sql0` |

Change workload size with `make test COUNT=2000000`. `BULK=0` uses one INSERT per row within each transaction for a round-trip comparison. Reuse neither a journal nor a `RUN_ID`. This example fixes data-node membership; online data-node expansion and partition reorganization are not implemented here.

## Representative workload: subscriber profiles and online quota checks

`make test` now also loads **1,000,000 subscriber profiles** and runs the Go subscriber workload. `make subscriber-profiles` reruns it after provisioning, resetting only the subscriber table. Orders and subscriber profiles are separate datasets; default testing retains one million rows in each table, plus the customer table.

Each profile has a subscriber ID, a non-null numeric phone identifier, a deliberately unindexed alternate reference, quota and version. The primary key partitions by subscriber ID; the phone has a unique HASH index. This models keyed profile access and online quota authorization. It compares primary-key lookup, unique-phone lookup and lookup through the unindexed reference, verifying actual EXPLAIN access paths and returned state.

A mixed phase uses 8 clients for **8,000 reads and 2,000 atomic quota updates**. Each conditional UPDATE decrements quota and increments version only from version 0 with positive quota. Updates target distinct profiles, so this test does not establish behavior under hot-key contention. Every acknowledged update is fsynced to `subscriber-acknowledged.jsonl`. Final row-level predicates check the exact affected IDs and values, plus aggregate counts and sums.

2026-11-14, Apple M4 Pro, shared aarch64 Docker VM, 11 vCPUs / 24.4 GiB RAM, NDB 8.4.11 amd64 emulated, DataMemory 256 MiB per data node, no CPU caps. Run `20261114T191541Z`; profile load took **22.166 s**.

| Phase | Operations / clients | Operations/s | p50 / p99 / max |
|---|---|---:|---:|
| Primary-key profiles | 10,000 / 1 | 3,726 | 0.248 / 0.496 / 12.238 ms |
| Unique-phone profiles | 10,000 / 1 | 1,868 | 0.531 / 0.950 / 5.202 ms |
| Unindexed reference | 20 / 1 | 17.27 | 57.793 / 59.025 / 59.076 ms |
| 80% reads / 20% quota updates | 10,000 / 8 | 15,660 | 0.333 / 1.382 / 33.476 ms |

The unindexed lookup p50 was 233.04× the primary-key p50. Its plan scanned all three partitions with a pushed condition; primary lookup used one partition, and the unique-phone query used its hash index. Both key paths estimated one matching row. This illustrates the cost of turning a keyed online lookup into a distributed scan, rather than claiming that every NDB SQL workload has the same latency.

Verification found **1,000,000 profiles**, **2,000 distinct acknowledged updates**, **2,000 observed versions**, total quota **999,998,000**, and **0 incorrect rows**. The 2,000 journal entries were also audited against the exact expected subscriber IDs. This new profile workload did not inject failures; the separate order-based failure tests above retain their own measured coverage and disk-full loss result.

Defaults are `SUBSCRIBER_LOOKUPS=10000`, `SUBSCRIBER_SCANS=20`, and `SUBSCRIBER_OPERATIONS=10000`, capped by `COUNT`. These are Make variables. `subscriber-profiles.json` contains timings, counts and EXPLAIN plans. Lookup phases are sequential; mixed-request latency excludes the subsequent journal fsync, while mixed throughput includes it. Twenty scan samples do not establish stable tail latency. The full test and cleanup lifecycle passed.

Sources: [NDB index configuration](https://dev.mysql.com/doc/refman/8.4/en/mysql-cluster-ndbd-definition.html), [NDB scan and hash-index limitations](https://dev.mysql.com/doc/refman/8.4/en/mysql-cluster-limitations-performance.html), [8.4.11 SQL-to-NDB key-read path](https://github.com/mysql/mysql-server/blob/mysql-cluster-8.4.11/storage/ndb/plugin/ha_ndbcluster.cc). All query plans and numerical results above were measured locally.

## Verified test coverage

The following runs completed live against Docker deployments. Each reconciled **1,000,000 acknowledged and observed orders**, **0 missing acknowledged IDs**, **0 duplicate IDs**, **0 committed rows without a witness**, and **5,000,500,000 cents**. All nine NDB data nodes were STARTED, the tables had three partitions with three configured copies, and both SQL frontends matched. Physical replica contents were not independently queried.

| Executed scenario | Local run ID |
|---|---|
| Baseline | `20261114T174615Z` |
| Data-node failure, data1 | `20261114T174849Z` |
| SQL-frontend failure | `20261114T180634Z` |
| Management-node failure | `20261114T180748Z` |

Alternate data-node fault targets, the two-million-order variant and the row-at-a-time comparison were not run. The separate correlated disk-full run failed and lost acknowledged rows; it is documented below and excluded from the passing table.

Audit the raw evidence in `results/<run-id>/`: `load.json`, `check.json`, `acknowledged.jsonl` and, for fault/scaling runs, `failover.json`. Journals and raw reports remain local and are ignored by Git. Timing, phase latency, offered-rate limits and shared-VM conditions are detailed below; these runs do not provide a controlled cross-system ranking.

Batch latency includes transaction attempts and retries; it excludes the subsequent journal fsync, which is included in overall elapsed time and throughput.

## Measured results

2026-11-14, Apple M4 Pro, aarch64 Docker VM, 11 vCPUs and 24.4 GiB RAM, shared with other examples. NDB was amd64 emulated; no per-container CPU cap. One run per scenario, 1,000,000 orders, 10,000 customers, 8 clients and 100 rows/transaction. Latencies include retries and are grouped by the phase when a transaction completed.

| Scenario | Elapsed | Orders/s | Phase batch p50 / p99 / max | Retries / commit errors | Outage plus rejoin |
|---|---:|---:|---:|---:|---:|
| Baseline | 76.834 s | 13,015 | baseline 59.804 / 99.077 / 189.477 ms | 0 / 0 | — |
| Data-node failure | 107.257 s | 9,323 | 78.128 / 187.196 / 462.222 ms | 9 / 3 | 38 s |
| SQL-node failure | 67.903 s | 14,727 | 52.651 / 82.490 / 3,785.101 ms | 6 / 0 | 17 s |
| Management-node failure | 66.615 s | 15,012 | 51.699 / 99.300 / 171.536 ms | 0 / 0 | 14 s |

Each of these four completed runs acknowledged and observed **1,000,000 orders**, lost **0 acknowledged IDs**, found **0 duplicates** and summed **5,000,500,000 cents**. Initial partition counts were 332,100 / 333,000 / 334,900 orders. Higher rates in the later fault runs reflect changing shared-VM contention; these are recovery measurements, not controlled comparative benchmarks.

### Correlated storage failure: acknowledged data was lost

A separate SQL-failure run exhausted the Docker VM's 79 GiB filesystem. All nine data nodes crashed during local-checkpoint writes (`FSAPPENDREF`, OS error 28, filesystem full). Restarting the existing volumes without `--initial` recovered **442,300 of 449,100 acknowledged orders**: **6,800 acknowledged IDs were missing**, with 0 duplicates and 0 unacknowledged rows. Acknowledged amount was 2,241,629,550 cents; recovered amount was 2,202,866,150 cents. The requested million-order run did not finish, and reconciliation correctly failed.

That result concerns a common storage failure across every replica, rather than an isolated node outage. All containers share one Docker disk and one host, so three copies here do not provide independent storage failure domains. The isolated SQL and management tests above were rerun after freeing confirmed test-owned orphan volumes. Do not infer host-crash durability from the successful single-node tests. Raw logs, journals, exact missing IDs and JSON reports remain under the local ignored `results/` directories.

## NDB versus Vitess

| Aspect | This NDB example | Vitess customer-sharding example |
|---|---|---|
| Partitioning | NDB hashes `customer_id` into 3 engine partitions | VTGate hashes `customer_id` into 3 key ranges |
| Copies | 3 NDB data nodes per node group | 1 MySQL primary + 2 replicas per shard |
| SQL frontends | Shared NDB data from either SQL process | VTGate routes to independent MySQL shards |
| Failover | Data-node group continues using surviving copies | VTOrc promotes a replica and updates routing |
| Local baseline | 13,015 orders/s, p99 99.077 ms | 33,872 orders/s, p99 59.319 ms |

The observed rate ratio was 2.60×, but both runs shared a VM with varying contention and used amd64 emulation. It does not establish a product performance ranking. NDB's distributed transactions and Vitess's sharding model have different execution paths; this workload deliberately keeps each write transaction customer-local.

## Upstream references

- [Community versus commercial MySQL Cluster](https://www.mysql.com/products/cluster/faq.html): this example uses the free Community image, not CGE.
- [NDB 8.4 data-node parameters](https://dev.mysql.com/doc/refman/8.4/en/mysql-cluster-ndbd-definition.html): replica count, node groups and storage settings.
- [NDB partitioning](https://dev.mysql.com/doc/refman/8.4/en/mysql-cluster-nodes-groups.html): partitions, fragments and node groups.
- [8.4.11 distribution handler source](https://github.com/mysql/mysql-server/blob/mysql-cluster-8.4.11/storage/ndb/src/kernel/blocks/dbdih/DbdihMain.cpp): partition placement, replica membership and checkpoint recovery.
