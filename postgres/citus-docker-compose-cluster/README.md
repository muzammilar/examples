# Citus colocated orders, recovery and rebalancing

Citus 14.2.0 with three worker groups and a coordinator group. Each group has one PostgreSQL primary and two physical replicas managed by Patroni. A customer-local workload continues through promotion, replica stalls and worker-group rebalancing.

## Quick start

1. `make docker-up` — compile Citus and wait for 12 PostgreSQL instances, etcd and the coordinator router.
2. `make up` — provision distributed tables, build the Go client and seed 10,000 customers.
3. `make test` — write and reconcile 1,000,000 orders, run tenant dashboards/profile transactions and wait for every replica to catch up.
4. `make down` — drop the distributed tables.
5. `make docker-down` — remove this project's services, named volumes and locally built images, including scale-profile workers.

Requires Docker Compose v2 and Make. Build tools and PostgreSQL clients run in Docker. Allow 12 GiB RAM and at least 15 GiB free Docker disk space for the expanded topology. The first Citus build takes several minutes. SQL binds `127.0.0.1:5433`, overridable with `CITUS_PORT`; credentials are local example values in Compose.

## Placement and configuration

| Component | Configuration |
|---|---|
| Citus | 14.2.0 source archive, SHA-256 checked, compiled natively |
| PostgreSQL | Server and development headers both pinned to Debian package `18.7-1.pgdg12+2` |
| Coordinator | Group 0, 1 primary + 2 replicas, health-checked HAProxy 3.2.4 endpoint |
| Workers | Groups 1–3, 1 primary + 2 replicas each: 9 data-serving copies |
| Distributed tables | 30 logical shards per table; `customer_id` distribution key; customers/orders colocated |
| HA | Patroni 4.0.6 native Citus integration, 3 etcd 3.5.21 members |
| Commit | Strict synchronous mode, 1 synchronous standby, synchronous commit on; third copy asynchronous |
| Storage / recovery | Checksums, logical WAL for shard moves, pg_rewind, 512 MiB retained WAL per instance, rebuild on failed rewind |

Logical Citus shards are different from PostgreSQL copies. There are 30 order shards and 30 colocated customer shards across three worker groups. Physical streaming replication supplies the three copies within each group; this example does not use three independent Citus shard placements. The coordinator's three additional copies make **12 PostgreSQL instances** at baseline. Five worker groups require **18 instances** including the coordinator group.

Patroni owns promotion and updates Citus membership through its native integration. The router follows the coordinator primary; worker metadata follows promoted worker primaries. Customer-local transactions and joins remain colocated. Queries without the distribution key can require scatter work. Cross-shard transactions and full-host loss have additional failure modes beyond the process faults measured here.

The Docker-built Go client uses `database/sql` and pgx 5.7.6. It writes deterministic IDs and integer cents, retries uncertain commits with the same keys, and fsyncs every successful batch to a fresh host-mounted `results/<RUN_ID>/acknowledged.jsonl`. Reconciliation streams every joined row, checking IDs, customers, amounts, duplicates, missing acknowledged IDs and committed rows without a client witness. An incomplete load fails. Replica checks capture each active group's primary WAL position and wait for both standbys to replay through it.

## Experiments

| Command | Action |
|---|---|
| `make failover` | Kill the primary of worker group 1 during active traffic; restore and verify rejoin |
| `make failover FAULT_SHARD=2` | Target another worker group |
| `make failover-coordinator` | Kill the coordinator primary and verify the router follows promotion |
| `make chaos-replica-lag` | Pause a synchronous worker replica for 10 s; observe commit stalls and reconcile after recovery |
| `make scale-out` | Add worker groups 4 and 5 with three copies each; run `citus_rebalance_start()` / `citus_rebalance_wait()` under load |
| `make scale-in` | Drain groups 4 and 5 under load, finish deferred cleanup, disable metadata synchronization while reachable, stop and remove them |
| `make test COUNT=2000000` | Two-million-order correctness run |
| `make test BULK=0` | Row-at-a-time INSERT comparison inside each transaction |
| `make status` / `make cli` | Membership, placement and totals / coordinator SQL shell |

Defaults are `COUNT=1000000 CUSTOMERS=10000 WORKERS=8 BATCH=100 BULK=1 RATE=0`. Fault and scaling targets use `CHAOS_RATE=25000` records/s as an offered-rate ceiling. They do not pause the producer. Each test resets orders and creates an exclusive journal; use a fresh `RUN_ID` if setting it explicitly. The scaling sequence is `make scale-out` followed by `make scale-in` after normal provisioning.

## Representative workload: tenant dashboards

`make test` now runs the Go tenant-dashboard scenario automatically. Run `make tenant-analytics` separately after loading the order data. This models a SaaS customer's dashboard and profile changes over colocated customers/orders, using the existing million-order dataset.

The comparison requests the same customer's answer in two ways: aggregate all tenants and select the customer in Go, or include the customer key in SQL. Saved JSON EXPLAIN plans must show **30 distributed tasks versus 1 routed task**. A mixed phase then executes 200 tenant requests with 8 clients: 160 dashboard reads and 40 transactions that update a customer profile and read its joined orders. It verifies all 40 profile updates, every dashboard result and the original million-order acknowledgement journal after the changes.

2026-11-14, Apple M4 Pro, shared native aarch64 Docker VM, 11 vCPUs / 24.4 GiB RAM, 128 MiB shared buffers per PostgreSQL instance, no CPU caps. Run `20261114T191429Z`, 1,000,000 orders / 10,000 customers. Each read comparison has 25 sequential client requests; these are short, cache-affected observations, not capacity estimates.

| Phase | Requests | Operations/s | Batch/request p50 / p99 / max |
|---|---:|---:|---:|
| All tenants, then select one | 25 | 7.21 | 73.078 / 513.123 / 842.412 ms |
| Tenant key in SQL | 25 | 1,913.43 | 0.438 / 0.888 / 1.264 ms |
| Mixed tenant reads/profile transactions | 200 | 4,346.58 | 0.510 / 13.399 / 16.096 ms |

The tenant-filtered p50 was 166.84× lower in this run. A global dashboard genuinely requesting every tenant still needs its distributed aggregate; the comparison measures unnecessary fan-out when the application needs only one tenant. A hot tenant remains confined to one worker group. One local run does not establish scale-out capacity or predict production latency.

`ANALYTICS_QUERIES=25` and `ANALYTICS_REQUESTS=200` are Make variables; both must fit within `CUSTOMERS`, and `COUNT >= CUSTOMERS * BATCH` gives every customer orders. `results/<run-id>/tenant-analytics.json` contains phase timings and the complete plans. A distributed UPDATE using an implicit stable text conversion failed during development; profile values are now supplied as parameters, and setup uses a constant assignment. The full test and cleanup lifecycle passed after that fix.

Sources: [Citus distribution-column and colocation guide](https://docs.citusdata.com/en/stable/sharding/data_modeling.html), [14.2.0 router planner](https://github.com/citusdata/citus/blob/v14.2.0/src/backend/distributed/planner/multi_router_planner.c). The plan and operation counts above were measured locally.

## Verified test coverage

The following runs completed live against Docker deployments. Each reconciled **1,000,000 acknowledged and observed orders**, **0 missing acknowledged IDs**, **0 duplicate IDs**, **0 committed rows without a witness**, and **5,000,500,000 cents**. All configured replicas passed the example's catch-up checks.

| Executed scenario | Local run ID |
|---|---|
| Baseline | `20261114T174314Z` |
| Worker-primary failure | `20261114T174323Z` |
| Coordinator-primary failure | `20261114T174431Z` |
| Scale-out | `20261114T182637Z` |
| Scale-in | `20261114T182733Z` |
| Synchronous-replica pause | `20261114T182820Z` |

Other worker-group failure targets and the two-million-order variant were not run.

Audit the raw evidence in `results/<run-id>/`: `load.json`, `check.json`, `acknowledged.jsonl` and, for fault/scaling runs, `failover.json`. Journals and raw reports remain local and are ignored by Git. Timing, phase latency, offered-rate limits and shared-VM conditions are detailed below; these runs do not provide a controlled cross-system ranking.

Batch latency includes transaction attempts and retries; it excludes the subsequent journal fsync, which is included in overall elapsed time and throughput.

## Measured results

2026-11-14, Apple M4 Pro, shared native aarch64 Docker VM with 11 vCPUs / 24.4 GiB RAM; no per-container CPU cap, 128 MiB PostgreSQL shared buffers per instance. One million orders / 10,000 customers / 8 clients / 100 rows per transaction, one run per scenario. Other examples and builds shared the VM. Latencies include retries and use transaction completion phase.

| Final retained-WAL configuration | Elapsed | Orders/s | Retries | Promotion | Batch p50 / p99 / max |
|---|---:|---:|---:|---:|---:|
| Baseline | 4.735 s | 211,185 | 0 | — | 2.693 / 16.857 / 103.664 ms |
| Worker primary failure | 62.750 s | 15,936 | 1,245 | 21 s | failure 1.876 / 36.301 / 22,639.259 ms |
| Coordinator primary failure | 64.149 s | 15,589 | 1,407 | 21 s | failure 2.772 / 220.725 / 21,203.656 ms |

All three completed runs acknowledged and observed **1,000,000 orders**, with **0 missing acknowledged IDs**, **0 duplicates**, **0 rows without a client witness** and **5,000,500,000 cents**. Both failure runs had 0 uncertain commit responses. Every active group's replicas caught up through the captured WAL positions.

| Final repeated-cycle tests | Load elapsed | Orders/s | Operation duration | Operation-phase p50 / p99 / max |
|---|---:|---:|---:|---:|
| Scale 3 → 5 worker groups | 41.653 s | 24,008 | rebalance 21 s | 1.930 / 50.391 / 389.697 ms |
| Scale 5 → 3 worker groups | 41.458 s | 24,121 | drain/removal 23 s | 1.972 / 37.491 / 193.078 ms |
| Synchronous replica pause | 50.863 s | 19,661 | pause 10 s | 8.404 / 10,038.534 / 10,517.808 ms |

These three final runs each acknowledged and observed **1,000,000 orders**, with **0 missing acknowledged IDs**, **0 duplicates**, **0 rows without a client witness**, **0 retries**, **0 uncertain commit responses** and **5,000,500,000 cents**. All active replicas replayed through their captured WAL positions. Scaling results are paced at an offered ceiling of 25,000 records/s, not maximum-throughput measurements. Another corrected scale-in before the repeated full cycle completed in 26 s with the same million-order invariants.

## Issues found during testing

- Mixing PostgreSQL 18.0 runtime binaries with 18.7 development headers caused `undefined symbol: heap_datum_check`. Both runtime and headers are now pinned to the same package version before compiling Citus.
- An old primary initially could not rejoin because its requested WAL had already been removed. The final topology retains 512 MiB of WAL and enables Patroni's rewind/rebuild paths; worker and coordinator promotion were retested from fresh volumes. This retention window is not a backup or WAL archive.
- Stopping a drained worker before disabling metadata synchronization caused requests to its unavailable hostname. Scale-in disables it while reachable.
- Repeated scale-in exposed `orphaned shard ... leftover from the previous move could not be cleaned up`. Citus's cleaner skips records whose node group has been removed. Scale-in now explicitly cleans and verifies `pg_dist_cleanup` for each group **before removing it**. Removal checks current metadata because Patroni can concurrently remove a departed member. The failed initial repeat observed 343,000 orders against 341,900 witnessed acknowledgements: 0 acknowledged IDs missing, but 1,100 committed rows without a witness after the old harness stopped its producer, and an incomplete requested load. Reconciliation failed as intended. The harness now lets the producer finish even when a scaling operation fails, then verifies data and returns the scaling error. The corrected repeated scale cycle passed above.
- The shared Docker filesystem filled during an earlier extra-worker startup; that run failed and is not a passing scale result. All copies here share the same storage failure domain. Run examples sequentially and retain the documented disk headroom.
- Docker Desktop briefly exposed truncated phase labels across a host-file rename. The client now accepts complete phase names and retries a transient incomplete read. Original JSON reports are retained under ignored `results/`.

## Sources

- [Citus 14 utility functions](https://learn.microsoft.com/en-us/postgresql/citus/api-udf?view=citus-14): rebalance, drain, disable and remove operations.
- [Citus 14.2 shard transfer source](https://github.com/citusdata/citus/blob/v14.2.0/src/backend/distributed/operations/shard_transfer.c).
- [Citus 14.2 deferred cleanup source](https://github.com/citusdata/citus/blob/v14.2.0/src/backend/distributed/operations/shard_cleaner.c): cleanup registration and skipping removed node groups.
- [Patroni 4.0.6 Citus integration](https://github.com/patroni/patroni/blob/v4.0.6/patroni/postgresql/citus.py) and [upstream Citus Compose example](https://github.com/patroni/patroni/blob/v4.0.6/docker-compose-citus.yml).
