# MySQL NDB Cluster: partitioned orders and recovery

Three native NDB partitions with three fragment replicas each, two SQL frontends,
two management nodes and HAProxy. Go writers/readers retain order and subscriber
quota witnesses while native node failures and recovery are measured.

## Quick start

1. `make docker-up` — start and wait for all nine data nodes, both SQL/management frontends and HAProxy.
2. `make up` — build the local Go workload, record fixture/storage identities, create tables and seed 10,000 customers plus 1,000,000 subscriber profiles.
3. `make test` — append/reconcile 1,000,000 orders, then benchmark keyed subscriber access and versioned quota updates.
4. `make down` — verify retained history, then drop this fixture's application tables.
5. `make docker-down` — remove this project's containers, volumes, network and locally built workload tag.

Requires Docker Compose v2, Make, Python 3.10+ and gzip. The Oracle Community image
is amd64 and runs under emulation on Apple Silicon. SQL binds `127.0.0.1:13306`
(`NDB_PORT` overrides it); credentials and plaintext endpoints are local lab values.
Histories, NDB checkpoints and logs grow; the controller stops before Docker VM
free space falls below 6 GiB or native data-memory use exceeds 90%. These guards
are not pressure tests. Do not share this cluster with another example.

## Topology and limits

| Component | Version/configuration | Resource cap |
|---|---|---|
| Data nodes | Oracle Community Cluster 8.4.11, `data1`–`data9`, three explicit node groups | Each 1 CPU / 2 GiB |
| NDB storage | `NoOfReplicas=3`, `DataMemory=512M`, four 16 MiB redo files per log part, retained named volume per node | 4.5 GiB aggregate configured data memory |
| SQL | `sql0`/`sql1`, NDB API node IDs 51/52; 64 MiB InnoDB buffer pool for local SQL state | Each 1 CPU / 1 GiB |
| Management | `mgm0`/`mgm1`, node IDs 49/50 | Each 0.25 CPU / 256 MiB |
| Router | HAProxy 3.2.4, health checks and session shutdown on failed SQL backend | 0.25 CPU / 256 MiB |
| Go workload | `database/sql`, go-sql-driver/mysql 1.9.3, local Dockerfile/nonroot binary | Each 2 CPU / 1 GiB |
| Native fragment scanner | Pinned official `ndb_select_all`, reserved API node 53, one-shot Compose service | 1 CPU / 512 MiB |

NDB SQL frontends share distributed storage; they are not independent MySQL
shards. `PARTITION BY KEY(customer_id) PARTITIONS 3` selects native
`HashMapPartition` placement. An actual fragment has one current primary and two
backups in its native node group. Three replicas share this Docker VM and host;
this does not establish independent-host or power-loss durability. NDB durability
uses its own replication/checkpoint/redo protocol; InnoDB fsync settings do not
establish NDB's storage guarantees. The historical correlated disk-full loss is
retained below.

## Witnesses and native verification

`orders-workload/` holds the local Go writer, independent reader, full-body oracle,
mutable subscriber benchmark and native scan parser. `control.py` owns lifecycle,
operations and native barriers; `report.py` records phase observations. Everything
builds from this example, with no sibling Makefile, generated source or symlink.

A persistent fixture UUID binds the external witness to both SQL server UUIDs,
all nine owned data-volume identities and native table object IDs. Repeated setup
preserves data; it rejects legacy tables without their witness. Resumed seeds
validate every existing canonical body, then bulk-insert only missing keys. They
do not overwrite names, phone fields, quota or versions. Every order run
reserves disjoint IDs and fsyncs attempts/ACKs/errors outside the failed database
containers. Tests never truncate previous orders. Newly allocated keys use plain batched INSERTs so NDB can batch writes;
retry attempts use no-op duplicate handling to resolve uncertain commits without
overwriting bodies. Idempotent retries retain keys;
complete ambiguous commits are reported separately. An independent PRIMARY-path
reader checks immutable prior ACK bodies on each partition, or customer bodies on
a fresh fixture, with a 3 s deadline and 100 ms delay between probes. It keeps
observing while writes retry and through native repair/final verification.
Fresh-write visibility lag is not measured.

Full SQL scans through HAProxy and **both SQL frontends** check every historical
order ID/customer/amount, all customer names and all subscriber phone/reference/
quota/version bodies. Native EXPLAIN output records the expected partition for
every one of the 10,000 customer keys, using NDB's own hash implementation.
The pinned official native scan tool exports actual `FRAGMENT` values and row
bodies; compressed output is retained and streamed through a strict Go parser.
That independently checks actual customer-ID and order-body placement against the
native key oracle. Missing scan completion, truncation, count mismatch, wrong
fragments, missing ACKs, duplicate/unattempted IDs and partial transactions fail.

Explicit SQL `PARTITION(...)` scans are rejected for this native KEY layout, so
they are not used as a placement oracle. Native scans read **one logical copy per
fragment**, not every physical replica's body. Separately, `ndbinfo.nodes`,
`table_fragments`, `table_replicas`, dictionary/process metadata and restart
information must show all nine data nodes STARTED, all three live replicas per
fragment in the expected group, zero dead replicas and matching storage/table
identities. Profile bodies are checked globally; per-profile fragment mapping is
not independently audited. Do not call SQL readback a nine-copy byte comparison.

## Operational targets

| Command | Does |
|---|---|
| `make test-production` | Serial baseline/profile workload, current-primary crash on each of three partitions, both SQL frontends, both management nodes, router crash, another profile version and final historical verification |
| `make failover FAULT_PARTITION=0` | Discover and SIGKILL this partition's current primary; prove exit 137 and native promotion/two surviving replicas; require reads/writes while it stays down, then restore its volume |
| `make chaos-sql FAULT_NODE=sql0` | Require live application sessions on the SQL victim, crash it and observe HAProxy/client recovery |
| `make chaos-management FAULT_NODE=mgm0` | Crash that management process while SQL traffic continues |
| `make router-failure` | Crash HAProxy for 10 s and record failed requests separately from successful requests |
| `make subscriber-profiles` | Benchmark native primary/unique-hash keys versus scans, then append versioned quota mutations without resetting profiles |
| `make benchmark` | Append another order history without a dispatch ceiling |
| `make verify` | Reconcile all order/profile witnesses, full SQL bodies and native row/fragment metadata |
| `make status` / `make cli` | Native NDB membership / SQL shell |
| `make unit-test` | Docker formatting/vet/test/build plus controller/report negative controls |

Defaults: `COUNT=1000000 CUSTOMERS=10000 SUBSCRIBERS=1000000 WORKERS=8 BATCH=100`;
`CHAOS_RATE=25000` is the fault workload's dispatch ceiling. Live runs reserve
`MAX_ROWS=10*COUNT`, keep writing throughout repair and stop only after controller
request and the minimum count. Each fault holds its victim down for 10 s after
required degraded progress (router uses a fixed 10 s outage). Native repair and
post-repair progress must finish while both observers remain live. Thirteen
unrelated container/process identities must stay unchanged; data-node child PID
start times detect internal restarts hidden by an unchanged angel/container.

Failed cases retain witnesses/logs and restore only their target. Writers abort
before a potentially slow cleanup once a case fails. A retained-volume process
restart is not a disk replacement. Graceful/rolling maintenance, native node-group
expansion/reorganization, partitions, quorum/minority rejection, permanent disk
loss/write-I/O failure, bounded pressure, upgrades, independent restore and
controlled single/batch or cold/warm comparisons remain separate, **unrun** cases.

## Subscriber workload

One million profiles retain their bodies between runs. Primary-key and unique
phone-HASH lookups are compared with an unindexed reference scan, with native
EXPLAIN plans validated. Defaults are `SUBSCRIBER_LOOKUPS=10000`,
`SUBSCRIBER_SCANS=20`, `SUBSCRIBER_OPERATIONS=10000`. The mixed phase uses eight
clients for 80% reads and 20% quota updates on distinct IDs; hot-key contention is
unmeasured. Fault injections keep order writes/reads live and verify retained
profiles; quota mutations run before/after the fault matrix, not during outages.
Mutations compare the witnessed old quota/version, decrement once and
assign a new version. Intended and acknowledged bodies are fsynced before/after
the mutation. New runs verify all earlier bodies and refuse unresolved profile
results. A version gap does not imply multiple quota decrements.

Full readback checks every profile, including unchanged IDs, and the latest ACK
version/body. Each run preserves its allocation, EXPLAIN plans, raw latency samples
and outcomes under `profiles/`. Percentiles use nearest rank; p99 of 20 scan
samples is their maximum and does not establish stable tail latency. Pure lookup
latency measures the request; mixed latency/throughput include witness fsync.

## Measured recovery results

2026-11-16, Apple M4 Pro, Docker 29.5.3 aarch64 VM, **11 vCPUs / 24.4 GiB**;
NDB/MySQL amd64 emulated, Go native. Effective caps: 14 infrastructure services
have an aggregate **11.75 CPU / 20.75 GiB** quota; two concurrent Go observers add
4 CPU / 2 GiB. Native scan export is serial with the Go verifier and has 1 CPU /
512 MiB; host gzip/controller work also contributes overhead. Caps are not
reserved cores. Eight writer connections, 100 rows per transaction, 10,000
customers, one million retained profiles. Fault dispatch is capped at 25,000
rows/s. Datasets grow between cases; these are one-host recovery observations,
not controlled product capacity comparisons.

The latest complete reconciliation of fixture
`22d4848a-8e40-4943-b022-d4e711759fbd` retained **11,721,100 ACK
orders across 12 histories**, all 10,000 customers and one million
profile bodies. Missing ACKs, wrong values, misplaced orders/customer IDs,
duplicates, unattempted rows and partial transactions were **0**. Complete
committed-without-ACK orders: **0**; profile bodies
without an ACK: **0**. Native membership and
three-live-copy placement passed for all three partitions. This verifies logical
bodies and native placement/repair, not independent body reads of all nine copies.
Peak per-node data-memory use at that reconciliation was **331.562 MiB of 512 MiB**.

| Completed case | New ACK orders | Writer elapsed s | Orders/s | Failed attempts / ambiguous commits | Promotion s | Native repair barrier s | Longest read-success gap s |
|---|---:|---:|---:|---:|---:|---:|---:|
| Initial baseline | 1,000,000 | 6.664 | 150,057 | 0 / 0 | — | — | 0.328 |
| Aggregate rerun baseline | 1,000,000 | 6.626 | 150,918 | 0 / 0 | — | — | 0.328 |
| Data-primary crash p0 | 1,168,600 | 46.877 | 24,929 | 6 / 0 | 0.419 | 3.076 | 0.329 |
| Data-primary crash p1 | 1,299,700 | 52.177 | 24,909 | 2 / 0 | 0.415 | 3.419 | 0.330 |
| Data-primary crash p2 | 1,171,100 | 47.065 | 24,883 | 5 / 2 | 0.387 | 3.252 | 0.326 |
| SQL crash `sql0` | 1,000,000 | 40.112 | 24,930 | 1 / 0 | — | 3.466 | 3.620 |
| SQL crash `sql1` | 1,000,000 | 40.140 | 24,913 | 1 / 1 | — | 3.459 | 3.619 |
| Management crash `mgm0` | 1,000,000 | 40.147 | 24,908 | 0 / 0 | — | 3.355 | 0.335 |
| Management crash `mgm1` | 1,000,000 | 40.103 | 24,936 | 0 / 0 | — | 3.439 | 0.333 |
| HAProxy crash | 1,000,000 | 50.864 | 19,660 | 678 / 0 | — | 3.376 | 10.983 |
| Post-recovery benchmark | 1,000,000 | 6.512 | 153,562 | 0 / 0 | — | — | 0.325 |

Promotion includes the SIGKILL RPC and 0.5 s native polling. Repair timing begins
after Compose reports the restored victim healthy and includes full native
membership/fragment barriers under load; total restoration is longer. Read gaps
include serial three-partition probing and its deadlines. Full case elapsed time
also includes historical verification. Successful write-attempt latency excludes
witness fsync/preceding retries; writer throughput includes witness fsync. Raw
phase reports retain retry-inclusive batch latency and success/error rates.
Nearest-rank **p50 / p99 ms (sample count)** follows, separated by outcome.

| Case | Write success | Write errors | Read success | Read errors |
|---|---:|---:|---:|---:|
| Initial baseline | 2.501 / 8.781 (10,000) | — | 0.961 / 4.109 (271) | — |
| Aggregate rerun baseline | 2.603 / 8.664 (10,000) | — | 1.076 / 4.521 (330) | — |
| Data-primary crash p0 | 2.140 / 5.331 (11,686) | 22.257 / 51.211 (6) | 0.915 / 3.619 (753) | — |
| Data-primary crash p1 | 2.123 / 7.495 (12,997) | 14.579 / 17.788 (2) | 0.922 / 3.826 (862) | — |
| Data-primary crash p2 | 2.148 / 5.937 (11,711) | 100.248 / 119.226 (5) | 0.939 / 2.921 (859) | — |
| SQL crash `sql0` | 2.230 / 5.753 (10,000) | 3975.843 / 3975.843 (1) | 0.959 / 2.258 (791) | 1.622 / 3001.016 (2) |
| SQL crash `sql1` | 2.224 / 7.125 (10,000) | 37.677 / 37.677 (1) | 0.979 / 3.973 (836) | 3002.200 / 3002.200 (1) |
| Management crash `mgm0` | 2.227 / 6.104 (10,000) | — | 0.973 / 2.104 (903) | — |
| Management crash `mgm1` | 2.227 / 9.093 (10,000) | — | 1.004 / 3.565 (943) | — |
| HAProxy crash | 2.240 / 7.686 (10,000) | 6.031 / 29.452 (678) | 1.003 / 2.955 (980) | 8.247 / 33.884 (96) |
| Post-recovery benchmark | 2.783 / 8.647 (10,000) | — | 1.097 / 2.911 (690) | — |

| Completed case | Local run ID |
|---|---|
| Initial baseline | `20261116T094539-6b21d743` |
| Aggregate rerun baseline | `20261116T095044-d451ad17` |
| Data-primary crash p0 | `20261116T095251-b0ff8be8` |
| Data-primary crash p1 | `20261116T095439-d7c1936e` |
| Data-primary crash p2 | `20261116T095645-f850d37b` |
| SQL crash `sql0` | `20261116T095854-32622e3d` |
| SQL crash `sql1` | `20261116T100104-b6b72a13` |
| Management crash `mgm0` | `20261116T100323-1dc9ec8e` |
| Management crash `mgm1` | `20261116T100549-ea5e7a01` |
| HAProxy crash | `20261116T100825-2c76eddc` |
| Post-recovery benchmark | `20261116T101437-ae3e9b64` |

### Current subscriber measurements

Each completed version acknowledges 2,000 distinct quota updates by default and
retains older witnesses. Full readback checks every profile against the latest
ACK body. The mixed phase includes witness fsync. Twenty scan samples yield a
nearest-rank p99 equal to their maximum; they do not establish a stable tail.

| Version / path | Requests | Requests/s | p50 / p99 / max ms |
|---|---:|---:|---:|
| v1 / mixed 80 percent reads 20 percent updates | 10,000 | 5,563.75 | 0.530 / 29.107 / 48.606 |
| v1 / primary key | 10,000 | 2,123.53 | 0.426 / 0.846 / 37.855 |
| v1 / unindexed reference | 20 | 17.02 | 58.771 / 60.884 / 60.884 |
| v1 / unique phone | 10,000 | 1,417.67 | 0.699 / 1.080 / 8.606 |
| v2 / mixed 80 percent reads 20 percent updates | 10,000 | 5,614.24 | 0.525 / 22.252 / 71.566 |
| v2 / primary key | 10,000 | 2,120.22 | 0.435 / 0.854 / 5.829 |
| v2 / unindexed reference | 20 | 17.09 | 58.374 / 59.553 / 59.553 |
| v2 / unique phone | 10,000 | 1,405.23 | 0.700 / 1.080 / 38.589 |
| v3 / mixed 80 percent reads 20 percent updates | 10,000 | 5,820.47 | 0.525 / 25.910 / 44.452 |
| v3 / primary key | 10,000 | 2,108.27 | 0.444 / 0.785 / 25.396 |
| v3 / unindexed reference | 20 | 15.83 | 59.956 / 101.126 / 101.126 |
| v3 / unique phone | 10,000 | 1,400.28 | 0.706 / 1.073 / 2.643 |

## Fresh-volume lifecycle

Retained-state `make up`, status, unit checks and teardown passed. Cleanup left
zero owned containers, volumes, networks or workload tags. The complete
five-command lifecycle then created new fixture
`2e212a40-ddd6-410e-8d0a-b2b31169c316`, with two new SQL UUIDs and all nine new
owned data-volume identities. One million profiles were initialized from zero in
**21.621 s**, with no existing rows reused.

Run `20261116T102201-52608096` acknowledged **1,000,000 orders in 6.603 s /
151,441 orders/s**, successful-attempt p50/p99 **2.649 / 6.392 ms**, with **0 retries,
ambiguous responses or reconciliation errors**. Actual native FRAGMENT counts
were **332,100 / 333,000 / 334,900 orders**. SQL bodies through both frontends,
native customer/order placement and all three live replicas per fragment passed.

The fresh subscriber run `20261116T102249-f9dcae93` retained all one million
profiles and **2,000 ACK quota updates** with **0 incorrect rows**:

| Path | Requests | Requests/s | p50 / p99 / max ms |
|---|---:|---:|---:|
| Primary key | 10,000 | 2,101.48 | 0.433 / 0.871 / 17.821 |
| Unique phone hash | 10,000 | 1,411.43 | 0.700 / 1.096 / 4.824 |
| Unindexed reference | 20 | 17.14 | 58.195 / 59.302 / 59.302 |
| 80% reads / 20% quota updates | 10,000 | 5,664.24 | 0.531 / 24.058 / 49.024 |

Final `down`/`docker-down` passed and again left **0 owned containers, volumes,
networks or workload tags**. Both fixtures' witnesses, native scans and logs remain
local under ignored `results/`.

## Findings and failed attempts

- Initial 1 GiB data-node caps were below NDB's logged 1,064 MiB minimum /
  1,192 MiB initial allocation. Native child processes died with signal 9 while
  their angel containers exited 0. Docker retained no OOM events, so the exact
  kill source is unproven; increasing caps to 2 GiB restored startup on the same
  volumes. Health checks and native child identity matter more than exit code 0.
- Native KEY tables report `HashMapPartition`; explicit SQL `PARTITION(...)`
  scans return error 1747. Pre-seed assertions originally rejected the table type,
  then were corrected against pinned source/runtime. The native FRAGMENT scan
  above replaces the unsupported SQL scan path.
- A successful empty initial `restart_info` query emits no mysql column header.
  Optional diagnostics accept that empty result; required inventories still
  enforce exact membership counts.
- Upserts caused NDB's duplicate-key peek path to disable batching. The slow seed
  was deliberately stopped with its data retained; the revised initializer
  verified **546,000 existing profiles** and inserted **454,000 missing rows in
  10.022 s**, without changing old bodies. A fresh-volume lifecycle separately
  measures full initialization. Fresh order keys also use native bulk INSERTs;
  retry upserts remain the conservative ambiguous-commit recovery path.
- The first data-primary attempt `20261116T094726-ce967d97` failed an overly
  strict degraded-state assertion before reaching its million-row minimum. Its
  **81,700 ACK orders** survived recovery and remain in every later reconciliation.
  The passing rerun records `no_of_replicas=2`, two live/one dead replica during
  outage, then requires three live/zero dead replicas after repair. The failed
  attempt remains failed; native repair does not retroactively qualify it.

Bootstrap logs, raw metadata, partial seed and failed run journals are retained
under ignored `results/`; no database reset was used to turn an attempt into a
pass. Pinned source explains [native key hashing and duplicate-key batching](https://github.com/mysql/mysql-server/blob/mysql-cluster-8.4.11/storage/ndb/plugin/ha_ndbcluster.cc),
[FRAGMENT scan output](https://github.com/mysql/mysql-server/blob/mysql-cluster-8.4.11/storage/ndb/tools/select_all.cpp)
and [native replica/restart metadata](https://github.com/mysql/mysql-server/blob/mysql-cluster-8.4.11/storage/ndb/src/kernel/blocks/dbdih/DbdihMain.cpp).

## Historical 2026-11-14 subscriber measurements

These are earlier README-recorded measurements, not new runs. Their original raw
report was not present at the documented run path during this backfill. The old
20-sample p99 used a floor index; current results above use nearest rank and keep
all samples. Historical numbers are retained without calling them a fair comparison.

The 2026-11-14 implementation loaded **1,000,000 subscriber profiles** and reset the subscriber table per benchmark. Its order/profile tests used separate fresh datasets. Those historical measurements follow; current targets above preserve both order and profile histories.

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

Those runs used `SUBSCRIBER_LOOKUPS=10000`, `SUBSCRIBER_SCANS=20` and `SUBSCRIBER_OPERATIONS=10000`, with profile size from `COUNT`. These are Make variables. `subscriber-profiles.json` contains timings, counts and EXPLAIN plans. Lookup phases are sequential; mixed-request latency excludes the subsequent journal fsync, while mixed throughput includes it. Twenty scan samples do not establish stable tail latency. The full test and cleanup lifecycle passed.

Sources: [NDB index configuration](https://dev.mysql.com/doc/refman/8.4/en/mysql-cluster-ndbd-definition.html), [NDB scan and hash-index limitations](https://dev.mysql.com/doc/refman/8.4/en/mysql-cluster-limitations-performance.html), [8.4.11 SQL-to-NDB key-read path](https://github.com/mysql/mysql-server/blob/mysql-cluster-8.4.11/storage/ndb/plugin/ha_ndbcluster.cc). All query plans and numerical results above were measured locally.

## Historical 2026-11-14 order coverage

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

### Historical order measurements

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
| Historical 2026-11-14 baseline | 13,015 orders/s, p99 99.077 ms | 33,872 orders/s, p99 59.319 ms |

The observed rate ratio was 2.60×, but both runs shared a VM with varying contention and used amd64 emulation. It does not establish a product performance ranking. NDB's distributed transactions and Vitess's sharding model have different execution paths; this workload deliberately keeps each write transaction customer-local.

## Upstream references

- [Community versus commercial MySQL Cluster](https://www.mysql.com/products/cluster/faq.html): this example uses the free Community image, not CGE.
- [NDB 8.4 data-node parameters](https://dev.mysql.com/doc/refman/8.4/en/mysql-cluster-ndbd-definition.html): replica count, node groups and storage settings.
- [NDB partitioning](https://dev.mysql.com/doc/refman/8.4/en/mysql-cluster-nodes-groups.html): partitions, fragments and node groups.
- [8.4.11 distribution handler source](https://github.com/mysql/mysql-server/blob/mysql-cluster-8.4.11/storage/ndb/src/kernel/blocks/dbdih/DbdihMain.cpp): partition placement, replica membership and checkpoint recovery.
