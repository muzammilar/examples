# PostgreSQL — customer sharding with PgDog and Patroni

Three PostgreSQL shards, **one primary + two replicas per shard**. PgDog routes a customer's orders to its native bigint hash shard; Patroni elects primaries using a three-member etcd quorum. This is a customer-local OLTP workload: independent customers use different shards, while one customer's transaction stays on one shard. It is separate from the PgDog pooling and Citus examples.

## Quick start

1. `make docker-up` — build and start nine databases, three etcd members and PgDog.
2. `make up` — establish the dataset identity, configure routing and seed 10,000 customers.
3. `make test` — append 1,000,000 orders and reconcile all historical acknowledgements through PgDog and every physical database copy.
4. `make down` — verify the retained dataset, then drop this example's tables.
5. `make docker-down` — remove only this Compose project's services, volumes and locally built images.

Requires Docker Compose, Make and **Python 3.11+** (standard library only). Go, PostgreSQL tools and application builds run in Docker. SQL listens on `127.0.0.1:6432`; override `PGDOG_PORT` if needed. Credentials are for this isolated lab.

Repeated `up`, `test`, failure and benchmark targets **preserve earlier orders and witnesses**. No test truncates tables. `down` is the explicit dataset cleanup boundary; `docker-down` destroys the owned volumes. Keep `results/` for diagnosis, including failed runs. Concurrent controllers are rejected by an example-local lock.

## Workload and configuration

| Setting | Default | Meaning |
|---|---:|---|
| `COUNT` | 1,000,000 | New orders per baseline/benchmark; minimum new orders per fault case |
| `CUSTOMERS` | 10,000 | Customers fixed at `make up`; changing an existing dataset is rejected |
| `WORKERS` | 8 | Concurrent Go writer connections |
| `BATCH` | 100 | Orders for one customer in each transaction |
| `CHAOS_RATE` | 25,000 | Fault workload's offered-rate ceiling in rows/s; backpressure can reduce it |
| `MAX_ROWS` | `10 * COUNT` | Reserved IDs for each live fault workload; exhaustion before recovery fails |
| `FAULT_SHARD` | 0 | Shard whose primary or current synchronous standby is targeted |
| `RUN_ID` | timestamp + random suffix | Unique name within the dataset's `runs/`; reuse is rejected |

Versions: **PostgreSQL 17.6, PgDog v0.1.62, Patroni 4.0.6, etcd v3.5.21**. The Go workload uses `database/sql` with pgx v5.7.6, built with Go 1.25.1 into a nonroot distroless container. Its Docker build runs formatting, vet and unit checks. Python controls lifecycle/faults; application writes, independent reads and full reconciliation are in `orders-workload/`.

Each batch contains one customer's `(customer_id, op_id, amount)` rows. IDs are explicitly allocated from disjoint ranges, including reservations belonging to failed cases. Retrying the same IDs uses the composite primary key and `ON CONFLICT DO NOTHING`; complete ambiguous commits are counted separately, while partial batches fail. Amounts are deterministic integer cents. There are no cross-shard transactions in this workload.

**Native INSERT rewriting must be enabled:**

```toml
[rewrite]
enabled = true
split_inserts = "rewrite"
```

The controller rejects a local configuration without it. Setup restarts the owned router before loading its schema; it does not restart databases. The writer rejects a bulk INSERT reporting more affected rows than intended, before commit. Routing correctness is also independently checked against PostgreSQL's native `satisfies_hash_partition`, rather than reimplementing PgDog's hash in Go or Python.

PgDog uses transaction pooling, `role = "auto"`, `lsn_check_delay = 0` and `read_write_split = "prefer_primary"`. It discovers promoted primaries; **it does not elect or fence them**. The configured pool is 16 connections per backend, with 1 s health checks, 5 s checkout and 10 s query deadlines.

Patroni uses strict synchronous mode, one synchronous standby and `synchronous_commit=on`. An ACK means primary + **one** eligible standby flush, not three synchronous copies. The third copy is asynchronous. If no eligible synchronous standby remains, writes block. Patroni rewinds/rejoins the restored old primary, or can rebuild its divergent copy. `wal_keep_size=512MB` is not a backup/archive policy.

## Independent failure tests

Run serially after `make up`:

| Command | Experiment |
|---|---|
| `make failover FAULT_SHARD=0` | Discover and SIGKILL the actual primary; prove exit 137, elect a different primary, require affected-shard read/write progress with the old primary still stopped, then restore it. Repeat with `1` and `2`. |
| `make chaos-replica-lag FAULT_SHARD=0` | Discover and pause the current synchronous standby for 10 s under load, prove the paused state, then unpause it. Repeat with `1` and `2`. |
| `make test-production` | Baseline, primary failure on each shard, synchronous-standby pause on each shard, final full verification. At least **7 million new orders** with defaults. |
| `make benchmark` | Append another million orders without an injected fault; keep the independent reader active. |
| `make verify` | Reconcile all historical ACKs and full bodies through the router and every physical copy. |
| `make unit-test` | Build/run Go formatting, vet and oracle checks; run Python controller/report checks. |
| `make status` / `make cli` | Owned services, shard totals, native replication and router role view / interactive SQL through PgDog. |

Each fault keeps its writer **and independent reader** running before injection, through the outage and native catch-up, and afterward. A writer does not stop at a fixed message count during repair: it continues until the controller signals completion and the minimum `COUNT` is met. Recovery requires progress on the affected shard. Unrelated containers and PostgreSQL process start identities must remain unchanged.

The reader checks known immutable prior ACK bodies, rotating shards with a 100 ms interval and a 3 s query deadline; a fresh fixture checks seeded customers instead. It does not retry application reads. This observes availability and incorrect/missing historical reads, **not fresh-write visibility lag**. One blocked read can delay the next shard's probe until its deadline.

Before another fault, all older histories must pass. After restoration, every shard needs exactly one native primary, two expected streaming peers and a synchronous standby. All replicas must replay through frozen primary WAL flush positions and retain their recorded physical system identifiers. Then the verifier reads customers and orders **separately**, checking each ID, customer, name, amount, native shard placement, duplicates, unknown attempts and transaction completeness on all nine copies. A colocated JOIN alone can hide stray orders on the wrong shard and is deliberately not the oracle.

## Evidence and measurement

`results/<dataset-UUID>/fixture.json` binds the external witness to the native identity table and three physical system identifiers. Setup refuses legacy tables without this witness. `routing.json` contains the independent native hash mapping. Each unique `runs/<RUN_ID>/` retains:

- `run.json`, fsynced `writes.jsonl` attempts/ACKs/errors, independent `reads.jsonl`, and workload logs;
- `load.json` with acknowledged rows, writer elapsed time, rows/s, retries and ambiguous commit responses;
- `result.json` with fault proof, election time, phases, native repair, full historical reconciliation and read/write observations; unsuccessful cases retain `failure.json` instead;
- full native/copy checks under the dataset's `checks/`; failed reports are never rewritten as successes.

The witness is outside the failed database containers. Every attempted write and response is fsynced. Per-attempt successful and failed SQL latencies are separate; successful batch completion also includes retries/backoff. Throughput includes witness fsync, but excludes the controller's later full scans. The result records overall controller time separately. Phases use actual response completion timestamps, so a request started during an outage can finish in recovery. Reports include per-shard p50/p99/max, errors, ambiguous commits and gaps between successful read completions. These sampled gaps are not exact outage durations. The recovery phase includes restart, native catch-up, progress and survivor checks; it is not a separately timed replay-only metric.

## Results

2026-11-16, Apple M4 Pro, native ARM64 Docker VM: **11 CPUs / 24.42 GiB**.
Each database is capped at **1 CPU / 1 GiB**, each etcd member at **0.25 CPU /
256 MiB**, PgDog at **1 CPU / 1 GiB**, and each independent workload container at
**2 CPUs / 1 GiB**. CPU caps can collectively exceed the VM's 11 CPUs. These are
one shared-host run per case, not isolated-host saturation or cross-product rankings.
Every row below appends **1,000,000 new orders**, using 10,000 customers, eight
writer workers and 100-row transactions. Fault cases are rate-limited; baseline
and benchmark are unthrottled. `Writer s` excludes later full verification.

| Case | Writer s | Orders/s | Retries / ambiguous commit responses | Promotion / pause | Successful batch p50 / p99 / max ms, including retries | Failed SQL request p99 ms |
|---|---:|---:|---:|---|---:|---:|
| Baseline | 5.999 | 166,699 | 0 / 0 | — | 2.522 / 13.433 / 60.056 | — |
| Shard 0 primary killed | 65.612 | 15,241 | 40 / 0 | 19.354 s | 1.629 / 10.714 / 25585.034 | 5021.031 |
| Shard 1 primary killed | 60.458 | 16,540 | 32 / 0 | 19.279 s | 1.609 / 10.342 / 20452.218 | 5010.616 |
| Shard 2 primary killed | 65.690 | 15,223 | 41 / 0 | 17.866 s | 1.584 / 10.139 / 25649.711 | 5018.773 |
| Shard 0 sync standby paused | 50.138 | 19,945 | 8 / 8 | 10 s hold | 1.638 / 14.377 / 10146.546 | 10008.262 |
| Shard 1 sync standby paused | 50.162 | 19,935 | 7 / 7 | 10 s hold | 1.784 / 10.433 / 10150.890 | 10010.028 |
| Shard 2 sync standby paused | 50.087 | 19,965 | 7 / 7 | 10 s hold | 1.732 / 10.107 / 10158.788 | 10007.590 |
| Post-recovery benchmark | 6.062 | 164,973 | 0 / 0 | — | 2.560 / 15.010 / 43.885 | — |

Independent reads span the load, fault, repair and final verification. Successful
and failed read latency remain separate. Gaps refer to the affected shard, or the
worst of all shards for unfaulted cases; shard rotation/deadlines affect resolution.
All reads returned **0 missing/wrong known bodies**.

| Case | Successful / failed reads | Successful read p50 / p99 ms | Failed read p99 ms | Longest successful-read completion gap s |
|---|---:|---:|---:|---:|
| Baseline | 175 / 0 | 0.396 / 1.627 | — | 0.310 |
| Shard 0 primary killed | 557 / 7 | 0.277 / 2.660 | 3008.309 | 22.927 |
| Shard 1 primary killed | 517 / 7 | 0.272 / 1.795 | 3008.892 | 22.593 |
| Shard 2 primary killed | 575 / 7 | 0.275 / 3.708 | 3012.217 | 23.256 |
| Shard 0 sync standby paused | 647 / 0 | 0.327 / 3.329 | — | 0.326 |
| Shard 1 sync standby paused | 661 / 0 | 0.335 / 2.644 | — | 0.329 |
| Shard 2 sync standby paused | 668 / 0 | 0.347 / 2.116 | — | 0.320 |
| Post-recovery benchmark | 247 / 0 | 0.387 / 0.830 | — | 0.311 |

After the matrix and benchmark, **8,030,000 cumulative ACK orders across 11
journals** survived, including the two explicitly repaired failed smoke runs.
The router and **all nine physical copies** passed: **0 missing ACKs, wrong
values, wrong-shard rows, unknown rows, duplicate IDs or partial transactions**;
all attempted-but-unacknowledged commits were eventually witnessed by successful
idempotent retries. Final native placement was **2,630,600 / 2,704,000 / 2,695,400
orders** and **3,277 / 3,368 / 3,355 customers** on shards 0/1/2, with identical
bodies on their two replicas. All six faults proved restored streaming redundancy,
a synchronous peer and unchanged unrelated survivors.

Maximum batch latency matters: the primary crashes stalled some batches for
**20.45–25.65 s**, although successful batch p99 stayed near 10 ms. Synchronous
standby pauses produced **22 ambiguous commit responses** across the three cases;
idempotent retries reconciled them without extra orders. API failures are reported
separately rather than making successful-operation percentiles appear faster.

| Case | Retained run ID within dataset `c99082a4-647b-417b-83c0-c812838a59fe` |
|---|---|
| Baseline | `20261116T052428-6839c33c` |
| Shard 0 primary killed | `20261116T052458-3583d97c` |
| Shard 1 primary killed | `20261116T052630-f64044d9` |
| Shard 2 primary killed | `20261116T052759-d1ade4ba` |
| Shard 0 sync standby paused | `20261116T052935-f93fea7b` |
| Shard 1 sync standby paused | `20261116T053058-bb83f8e0` |
| Shard 2 sync standby paused | `20261116T053224-5360885a` |
| Post-recovery benchmark | `20261116T053410-23761dba` |

The complete five-command lifecycle then passed again from **fresh volumes**
with the final source/configuration: another **1,000,000 ACK orders**, all nine
copies correct, **0 retries, ambiguous responses, missing/corrupt/misplaced or
duplicate rows**. Writer time was **5.988 s (166,994 orders/s)**. Retained evidence:
`results/ec4a522e-8a33-4080-bc77-f89b8c5f9e46/runs/20261116T053642-51156061/`.
`make status`, `make unit-test` and host `go test -race ./...` also passed.
Both datasets were verified before explicit table/volume cleanup; all original
reports remain on the host. Final inspection found **0 owned containers, volumes
or networks** remaining.

## Routing failure found and recovery

The new full-body oracle rejected two 10,000-order smoke runs with **20,000 wrong-shard duplicate rows per run**. Without native INSERT rewriting, this pinned configuration broadcast multi-value INSERTs to all three shards. A customer/order JOIN could hide those extra orders because only the correctly placed shard contained the matching customer.

Enabling rewriting and a hot `RELOAD` alone did not change the behavior observed in the second smoke run. Restarting the owned idle router with the enabled configuration did; setup now performs that restart. This is the observed recovery procedure, not a claim that every form of PgDog hot reload fails.

`repair_broadcast.py` is a deliberately restricted recovery record for those two named failed fixtures. Before deleting duplicates it verifies every acknowledged body's canonical native-hash copy, backs up all primary rows to a fsynced repair directory, rejects unknown/corrupt data or live workloads, and deletes only wrong-shard copies. It does **not** truncate, replay or reseed ACK rows. Each repair removed 20,000 redundant logical rows and preserved every canonical ACK; all nine copies then passed. Both original failure reports remain failures. The script refuses other datasets/run sets; it is not a general-purpose repair tool. A subsequent fresh-router smoke run retained all **30,000 cumulative ACKs** with zero duplicates or misplaced rows.

## Limits and remaining coverage

- These are one-host process-loss and process-pause experiments, not independent disk/host/network failure domains. The router is a single availability dependency. The witness survives these database faults, not loss of its host.
- Customer hashing spreads customers, not one large customer's work. Resharding, online shard addition/removal and distributed cross-shard atomicity are not qualified here. Citus/Vitess comparisons require matching workloads, durability and resource budgets; these measurements do not rank them.
- Network partitions, DCS/quorum loss, stale-primary write rejection, physical disk faults, rolling upgrades, bounded pressure, independent backup/restore and router loss remain separate unrun cases. A successful primary crash does not qualify them.
- Read probes sample immutable historical records; they do not exhaustively detect stale reads during the fault or measure visibility lag. Full historical body/placement scans occur after native repair.
- Hot-customer and two-million-per-case variants were not run in this qualification. Do not reuse the earlier pre-audit benchmark as a comparison: its workload reset data between cases, used a weaker JOIN oracle and had different resource limits.

## References

- [PgDog bulk INSERT rewriting](https://docs.pgdog.dev/features/sharding/cross-shard-queries/insert/)
- [PgDog sharded-table configuration](https://docs.pgdog.dev/configuration/pgdog.toml/sharded_tables/)
- [PgDog replication and failover boundaries](https://docs.pgdog.dev/features/load-balancer/replication-failover/)
- [PgDog v0.1.62 native routing source](https://github.com/pgdogdev/pgdog/tree/v0.1.62/pgdog/src/frontend/router/sharding)
- [PgDog v0.1.62 rewrite configuration source](https://github.com/pgdogdev/pgdog/blob/v0.1.62/pgdog-config/src/rewrite.rs)
- [PostgreSQL 17 native hash-partition helper](https://www.postgresql.org/docs/17/functions-info.html)
- [Patroni synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html)
- [Patroni v4.0.6 election/synchronous implementation](https://github.com/patroni/patroni/blob/v4.0.6/patroni/ha.py)

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
