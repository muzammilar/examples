# PgDog — transaction pooling and primary discovery

PgDog in front of **one PostgreSQL primary + two replicas**, with Patroni and a three-member etcd quorum. The representative workload is short-lived customer-dashboard connections: compare direct PostgreSQL admission with transaction pooling, then observe reads/writes through database and router outages. This example does not shard data.

## Quick start

1. `make docker-up` — render configuration, build Patroni and start three PostgreSQL copies, etcd and PgDog.
2. `make up` — establish the dataset identity, build the Go workload and seed 10,000 customers.
3. `make test` — append 1,000,000 orders, verify all retained ACKs on all three copies, and compare direct/pooled connection bursts.
4. `make down` — verify the complete retained history, then drop this example's tables.
5. `make docker-down` — remove only this project's services, volumes and locally built images.

Requires Docker Compose v2, Make and **Python 3.11+** (standard library only). Go and PostgreSQL tools run in Docker. SQL binds `127.0.0.1:6433`; override `PGDOG_PORT`. Credentials are local lab values. `orders-workload/` contains the independent Go writer, reader, verifier and burst workload, with its own Dockerfile and pinned module dependencies. There are no sibling runtime dependencies.

Repeated setup and test targets retain existing orders and witnesses. **No test truncates tables.** `down` is the explicit dataset cleanup boundary; `docker-down` destroys its volumes. Keep ignored `results/` for diagnosis. An example-local controller lock rejects concurrent operations.

## Configuration

| Setting | Default / purpose |
|---|---|
| Versions | PgDog v0.1.62, PostgreSQL 17.6, Patroni 4.0.6, etcd v3.5.21 |
| Workload build | Go 1.25.1, `database/sql` + pgx v5.7.6, nonroot distroless runtime; formatting/vet/unit checks in the Docker build |
| `COUNT`, `CUSTOMERS` | 1,000,000 new orders per finite case; 10,000 customers fixed at setup |
| `WORKERS`, `BATCH` | 8 concurrent writers; 100 rows for one customer per transaction |
| `POOL_SIZE` | 16 connections per backend; transaction pooling, minimum 1 |
| `CHAOS_RATE` | 25,000 offered rows/s during live operations; backpressure can reduce it |
| `MAX_ROWS` | Fault case reservation: `10 * COUNT`; exhaustion before native recovery fails |
| `RUN_ID` | Timestamp + random suffix by default; reuse fails, including a failed run's ID |
| `BURST_CLIENTS`, `BURST_REQUESTS` | 256 concurrent clients, 4,000 fresh-connection requests per path |
| Discovery | Three backend entries, `role="auto"`, `lsn_check_delay=0`, 1 s health checks |
| Timeouts | Connect 1 s, checkout 5 s, PgDog query 10 s; writer request 15 s, independent reader 3 s |
| Reads | `read_write_split="prefer_primary"`; explicit replica session in a separate test |

Patroni uses strict synchronous mode, `synchronous_node_count=1` and `synchronous_commit=on`. A successful commit is flushed on the primary and **one eligible synchronous standby**, not all three copies. The other replica is asynchronous. PgDog discovers roles and reconnects clients; **Patroni elects and rejoins primaries**. Checksums and `pg_rewind` are enabled; Patroni can rebuild a divergent replica. Retaining 512 MiB WAL is not a backup/archive policy.

`config/pgdog.toml.in` is tracked; Make renders the ignored `config/pgdog.toml`. A pool-size change validates TOML before updating it, issues native `RELOAD`, then checks live `SHOW CONFIG` and records `SHOW POOLS`. It does not restart the router. Setup can restart the idle owned router before workloads to apply rendered setup changes.

## Production and configuration tests

Run serially after `make up`:

| Command | Operation and checks |
|---|---|
| `make failover` | Discover and SIGKILL the actual primary; prove exit 137, elect a different primary, require degraded read/write progress with the old node down, then restore it. |
| `make chaos-replica-lag` | Discover and pause the synchronous standby for 10 s under load, prove its paused state, then unpause. |
| `make replica-crash` | Discover and SIGKILL the asynchronous replica; keep reads/writes active with it down, then restore the retained volume. |
| `make router-failure` | SIGKILL PgDog, hold it down 10 s, observe independent read errors and writer retries, then restart it; databases remain running. |
| `make pool-reload` | Apply pool sizes 4 then 16 during independent reads/writes, hold each for 5 s, verify native configuration and restore the original size without restarting services. |
| `make test-production` | Baseline/burst, all five live cases above, explicit replica checks and final full verification. At least **6 million new orders** by default. |
| `make verify` | Check every historical ACK/body through PgDog and all three physical PostgreSQL copies. |
| `make pool-size POOL_SIZE=4` / `make config` | Change the live pool size / show native configuration, pools and replication. |
| `make compare-pools` | Restart only the idle router between pool sizes 4 and 16, check actual native connection counts, then append a million orders with 64 writers at each size; restore the original setting even on error. This retains both histories. |
| `make benchmark` | Append a million orders without injection, with the independent reader running. |
| `make replica-reads` | Assert PgDog's explicit replica route reports recovery mode; prove both native standbys reject writes with SQLSTATE `25006`. |
| `make connection-burst` | Repeat the direct/pooled customer-total comparison using **all historical ACKs**, not the latest `COUNT`. |
| `make unit-test` | Docker Go formatting/vet/oracle checks plus Python controller/report/configuration guard tests. |
| `make status` / `make cli` | Owned services, native roles/counts/replication and router view / interactive SQL. |

Each live case keeps a writer and **separate reader process** running before injection, throughout the outage and native repair, and afterward. The writer continues until the controller signals completion and at least `COUNT` rows have been acknowledged; a fixed input cannot run out silently during recovery. Container IDs/start times/restart counts and PostgreSQL start identities of unrelated survivors must remain unchanged.

The reader samples immutable prior ACK bodies from up to 16 different customers, or seeded customers on the first run. It probes every 100 ms, has a 3 s deadline and no application retries. Wrong/missing known bodies fail the case. This observes historical read availability, **not fresh-write visibility lag**.

Before another case, all older histories must pass. Native recovery requires one primary, both expected streaming peers, a synchronous standby, matching physical system identifiers and replica replay beyond a frozen primary WAL flush position. The full verifier then reads **customers and orders separately**, checking every customer/name, order ID/customer/amount, duplicate, unknown attempt and partial transaction on every copy. A JOIN alone could hide stray rows.

### Pool shrinking is not immediate

The hot-reload experiment reported configured size **4** while `SHOW POOLS`
still showed **9** existing primary connections. The pinned source moves old
connections into replacement pools; its creation limit does not immediately
remove those reused connections. A successful `RELOAD` therefore proves the
setting changed, not that all old connections were drained.

The first two nominal pool-size benchmark runs are retained but **excluded from
the qualified cap comparison**; `qualification.json` records why. The corrected
`compare-pools` restarts the idle owned router between sizes, verifies live
configuration, and checks native `sv_total` before/after each run. Data/volumes
remain intact. That restart is setup for this benchmark, not part of the live
zero-restart `pool-reload` experiment. Point-in-time native counts are not a
continuous sampled connection maximum.

## Durable witness and measurements

- `results/<dataset-UUID>/fixture.json` binds a native identity table to its external witness and PostgreSQL physical identity. Legacy tables without that identity are rejected; setup does not silently adopt/reset them.
- Each `runs/<RUN_ID>/` has exclusive ID reservations and fsynced `writes.jsonl` attempts/ACKs/errors plus independent `reads.jsonl`. IDs from failed runs are never reused. Retrying identical composite keys uses `ON CONFLICT DO NOTHING`; complete ambiguous commits are counted separately, while partial batches fail.
- `load.json` records acknowledged rows, writer elapsed time, throughput, retries and ambiguous commit responses. `result.json` adds phase markers, actual fault proof, before/after survivor identities, native repair and full historical checks. Failures keep `failure.json`; cleanup restores the injected fault without resetting evidence.
- Per-phase reports separate successful/failed SQL and read p50/p99/max. Successful batch completion includes retry/backoff; overall writer throughput includes witness fsync and excludes later full verification. Overall controller time is recorded separately. Phases follow response completion time; raw events retain starts.
- The sampled successful-read completion gap is not an exact outage duration. Native barrier time starts after the restored container's health wait; phase markers and `native_repair_completed_at` retain the broader restoration interval.

## Representative workload: connection bursts

Each request opens a new `database/sql` connection, reads a customer's total order amount, validates it against **all retained ACK histories**, and closes it. Client-side reuse is disabled. Both paths use the same SQL/data, PostgreSQL `max_connections=100`, and the same workload container budget. Only direct PostgreSQL `53300` capacity errors are accepted; unexpected errors/wrong bodies fail, and PgDog must serve every request. There are no application retries in this experiment.

A pre-established primary connection samples backend counts every 20 ms. Reports include successful/failed latency separately, SQLSTATE counts, actual sample counts and monitoring errors. Sampled peaks are lower bounds, not exact connection maxima. Results live under `checks/connection-burst-*/connection-burst.json` and are never overwritten. Read-only burst comparisons do not add orders or replace the live fault generator.

## Measured results

2026-11-16, Apple M4 Pro, native ARM64 Docker VM: **11 CPUs / 24.42 GiB**.
Each PostgreSQL copy and PgDog are capped at **1 CPU / 1 GiB**, each etcd member
at **0.25 CPU / 256 MiB**, and each workload container at **2 CPUs / 1 GiB**.
One shared-host run per case, not a saturation/capacity ranking. Each row appends
**1,000,000 new orders**, 10,000 customers, 100 rows per transaction; eight writers
except the labeled 64-writer pool cases. Fault/reload runs are rate-limited to
25,000 offered rows/s; baseline/pool benchmarks are unthrottled. Writer time includes
witness fsync and excludes later native/full-copy verification.

| Case | Writer s | Orders/s | Retries / ambiguous commit responses | Successful batch p50 / p99 / max ms, including retries | Failed SQL request p99 ms |
|---|---:|---:|---:|---:|---:|
| Baseline | 5.987 | 167,040 | 0 / 0 | 2.509 / 18.114 / 44.551 | — |
| Primary killed | 65.702 | 15,220 | 41 / 0 | 1.554 / 12.816 / 25668.151 | 5034.005 |
| Synchronous standby paused | 50.263 | 19,896 | 5 / 5 | 1.566 / 13.537 / 10162.389 | 10007.318 |
| Asynchronous replica killed | 40.148 | 24,908 | 0 / 0 | 1.530 / 16.208 / 10005.631 | — |
| Router killed | 50.542 | 19,786 | 186 / 0 | 1.593 / 12.933 / 11303.340 | 63.914 |
| Live pool 4 → 16 → original | 40.055 | 24,966 | 0 / 0 | 1.613 / 12.690 / 49.309 | — |
| Pool 4, 64 writers | 6.035 | 165,702 | 0 / 0 | 18.634 / 43.746 / 58.218 | — |
| Pool 16, 64 writers | 6.614 | 151,186 | 0 / 0 | 20.249 / 196.089 / 305.852 | — |
| Final benchmark, 8 writers | 6.375 | 156,865 | 0 / 0 | 2.595 / 16.540 / 245.038 | — |

The primary promoted in **20.624 s**. Its restored old primary (`s0n1`) later
became the asynchronous replica targeted by the separate replica-crash test;
all three copies were checked after both operations. The synchronous pause/router
outage each held the target for **10 s**. The live pool change held each setting
for **5 s**, with **0 retries/read errors** and all seven service/process identities
unchanged. For the corrected 64-writer comparison, primary `sv_total` was **2 → 4**
at size 4 and **2 → 16** at size 16; all three pools passed the native before/after
cap check. One observation does not establish an optimal pool size.

Independent reads cover load, outage, native recovery and full verification.
There were **0 missing/wrong known bodies**. Successful and failed request latency
are separate; sampled gaps include probe scheduling and the 3 s timeout.

| Case | Successful / failed reads | Successful read p50 / p99 ms | Failed read p99 ms | Longest successful-read completion gap s |
|---|---:|---:|---:|---:|
| Baseline | 115 / 0 | 0.359 / 3.006 | — | 0.109 |
| Primary killed | 494 / 8 | 0.288 / 2.746 | 3005.846 | 21.961 |
| Synchronous standby paused | 562 / 0 | 0.286 / 2.248 | — | 0.112 |
| Asynchronous replica killed | 473 / 0 | 0.264 / 0.982 | — | 0.119 |
| Router killed | 480 / 90 | 0.277 / 0.936 | 59.088 | 10.613 |
| Live pool 4 → 16 → original | 490 / 0 | 0.263 / 1.117 | — | 0.108 |
| Pool 4, 64 writers | 190 / 0 | 0.363 / 15.829 | — | 0.118 |
| Pool 16, 64 writers | 201 / 0 | 0.381 / 36.038 | — | 0.333 |
| Final benchmark, 8 writers | 208 / 0 | 0.338 / 0.969 | — | 0.106 |

Final reconciliation retained **12,000,000 cumulative ACK orders across 12
histories**: **0 missing ACKs, corrupt bodies, duplicate IDs, unknown rows,
misplaced rows, partial transactions or unresolved committed-without-ACK rows**
through PgDog and all three physical copies. Both standbys caught up, and both
rejected writes with native SQLSTATE **25006**. Repeated `make up` preserved the
entire dataset. All five live cases preserved unrelated survivor identities.

The two initial nominal cap comparisons and an earlier eight-writer benchmark
remain in the history but are omitted from the table above. The former have
`qualification.json` explicitly excluding them from cap-comparison claims; their
correctness checks still passed. Final results use the corrected native-cap runs.

| Case | Retained run ID in dataset `a6d40f84-368d-435d-8218-2c9b0dcc998e` |
|---|---|
| Baseline | `20261116T054850-a1b551b0` |
| Primary killed | `20261116T054929-6b3f88a0` |
| Synchronous standby paused | `20261116T055047-53496294` |
| Asynchronous replica killed | `20261116T055152-f6859bc5` |
| Router killed | `20261116T055248-5a79ea4f` |
| Live pool 4 → 16 → original | `20261116T055357-db1c421b` |
| Pool 4, 64 writers | `20261116T055920-edd9e577` |
| Pool 16, 64 writers | `20261116T060001-4d8fdbac` |
| Final benchmark, 8 writers | `20261116T060039-7b44b4ed` |

### Connection bursts

4,000 requests per path / 256 concurrent clients / fresh connection per request.
Direct runs first, then pooled; neither is a controlled cold-cache query profile.
Totals were independently derived from all acknowledged orders, including older
fault cases. Both monitors reported **0 errors**.

| Dataset | Path | Success / attempts | Capacity errors | Elapsed s | Successful requests/s | Successful p50 / p99 / max ms | Failed p50 / p99 / max ms | Sampled peak backends |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 million orders | direct_postgres | 3,728 / 4,000 | 272 | 15.506 | 240.4 | 993.630 / 2286.865 / 3509.418 | 314.418 / 1695.803 / 1797.517 | 49 |
| 1 million orders | pgdog_transaction_pool | 4,000 / 4,000 | 0 | 2.127 | 1,880.2 | 121.503 / 214.437 / 227.296 | — | 19 |
| 12 million orders | direct_postgres | 3,537 / 4,000 | 463 | 19.685 | 179.7 | 1284.868 / 2514.349 / 3806.716 | 691.664 / 1897.615 / 2002.979 | 88 |
| 12 million orders | pgdog_transaction_pool | 4,000 / 4,000 | 0 | 2.036 | 1,964.9 | 117.839 / 208.915 / 221.079 | — | 19 |

On the 12-million-order data, the direct/pooled monitors completed **383 / 101**
successful samples; the first baseline used **330 / 106**. Pooling admitted every
request in these bursts while direct PostgreSQL hit `53300`. This shows connection
admission/backend reuse, not a general query speedup or equal-budget product ranking.

The final five-command lifecycle then passed from **fresh volumes** with another
**1,000,000 ACK orders**, all three copies correct, **0 retries, ambiguous commit
responses, missing/corrupt/duplicate/partial rows**. Writer time was **6.080 s
(164,467 orders/s)**. The fresh burst again served **4,000/4,000** through PgDog
versus **3,660/4,000** direct, with **340** SQLSTATE 53300 errors. Evidence:
`results/94a6ee31-bd97-4e44-ac73-f344a20739da/runs/20261116T060335-02164e81/`.
Go formatting/vet/oracle checks, host race checks, six Python guard/report tests,
repeated setup, status/config and idempotent cleanup passed. Both datasets were
fully verified before explicit table/volume cleanup. Final inspection found **0
owned containers, volumes, networks or built image tags** remaining; reports remain
on the host.

## Limits and remaining coverage

- One physical host/disk and a single PgDog endpoint remain shared failure domains. Process crashes, a process pause and router loss do not establish independent host/disk/network durability. The witness is external to database containers, not independent of the host.
- This is one replicated PostgreSQL group. It does not shard data, add write shards or qualify resharding; use the separate customer-sharding example for that model. Pooling limits backend churn, not query work or sustained capacity.
- DCS/quorum loss, peer partitions/stale-primary fencing, permanent disk loss, rolling upgrades, resource pressure and independent backup/restore remain unrun. No claim that this five-case matrix closes those requirements.
- Explicit replica routing/rejection checks do not establish read-your-writes or freshness. Live readers sample immutable older bodies; exhaustive body checks happen after native repair.
- Pool comparison runs append data serially, so retained dataset size grows. Query cache state is not controlled; these are not cold-query benchmarks. Single-record (`BATCH=1`) throughput is not qualified here. They are configuration observations, not controlled capacity rankings. Earlier pre-audit results used resets, a weaker JOIN verifier and uncapped services; do not compare their timings with these runs.
- Two-million-per-case and hot-customer variants were not run in this qualification. The burst controller requires fully witnessed totals and rejects unresolved committed-without-ACK rows rather than inventing an expected sum.

## References

- [PgDog transaction mode](https://docs.pgdog.dev/features/connection-pooler/transaction-mode/)
- [General settings](https://docs.pgdog.dev/configuration/pgdog.toml/general/) and [native configuration inspection](https://docs.pgdog.dev/administration/config/)
- [PgDog v0.1.62 pool limits and waiter handoff](https://github.com/pgdogdev/pgdog/blob/v0.1.62/pgdog/src/backend/pool/inner.rs)
- [PgDog v0.1.62 reload/replacement implementation](https://github.com/pgdogdev/pgdog/blob/v0.1.62/pgdog/src/backend/databases.rs)
- [Patroni synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html) and [v4.0.6 election implementation](https://github.com/patroni/patroni/blob/v4.0.6/patroni/ha.py)

## Future work / TODO

Deferred checks for supported configurations; these are **not executed coverage**.
Keep recorded passes, failures and limitations above. Performance work requires
a separately selected depth; this list does not restart paused experiments.

- [ ] Exercise DCS/quorum loss, peer partitions and stale-primary fencing while independent pooled readers/writers remain live.
- [ ] Replace a safely fenced PostgreSQL data volume and verify native catch-up plus service from the rebuilt copy.
- [ ] Test supported rolling upgrades, bounded resource pressure and independent backup/restore.
- [ ] Keep backend-pooling/connection-storm observations distinct from data sharding; this example remains one replicated database group.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
