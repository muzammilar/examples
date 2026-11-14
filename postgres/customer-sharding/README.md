# PostgreSQL — customer sharding with PgDog and Patroni

Three Postgres shards, each with one primary and two replicas. PgDog hashes `customer_id` to a shard; Patroni elects its primary using a three-member etcd quorum. This example is separate from the PgDog pooling and Citus deployment examples.

## Quick start

1. `make docker-up` — build the Patroni image and wait for all nine databases, etcd and PgDog.
2. `make up` — provision both tables on each shard, reload PgDog's schema, build the Go client and seed 10,000 customers.
3. `make test` — write 1,000,000 orders, reconcile acknowledged IDs, verify all replicas caught up and verify each customer lives on exactly one shard.
4. `make down` — drop the example tables, leaving the services running.
5. `make docker-down` — remove the Compose services, volumes and example-built images.

Requires Docker Compose, Make, a POSIX shell and standard host tools (`awk`, `date`, `seq`, `wc`). Go and Postgres tools run in Docker. SQL is exposed at `127.0.0.1:6432`; override `PGDOG_PORT` if needed. Passwords are local demonstration credentials.

## Workload and configuration

| Setting | Default | Purpose |
|---|---|---|
| `COUNT` | 1,000,000 | Unique order IDs per run |
| `CUSTOMERS` | 10,000 | Customers seeded through PgDog |
| `WORKERS` | 8 | Concurrent client connections |
| `BATCH` | 100 | Orders for one customer per transaction |
| `BULK` | 1 | Multi-value INSERT per batch; `0` sends one INSERT per row |
| `RATE` / `CHAOS_RATE` | 0 / 25,000 | Unthrottled baseline / fault-run offered-rate ceiling in records/s |
| `FAULT_SHARD` | 0 | Shard whose primary or synchronous replica is targeted |
| `RUN_ID` | UTC timestamp | Unique `results/<RUN_ID>/` directory; never reused |
| PgDog | `v0.1.62` | Transaction pooling, automatic primary discovery, bigint hash routing |
| Postgres | `17.6` | Physical streaming replication; data checksums enabled |
| Patroni | `4.0.6` | Elections, promotion and rewind of the old primary |
| etcd | `v3.5.21` | Three-member DCS quorum |

The tables share a `customer_id` sharding rule. `customers` has a primary key on that column; `orders` has a composite primary key on `(customer_id, op_id)`. The client uses explicit IDs and integer cents. A customer-scoped join stays on its shard; a query without the key requires scatter work. The verifier reads the colocated join and compares every ID, customer and amount with the deterministic workload and its acknowledgement journal.

PgDog's `role = "auto"` and `lsn_check_delay = 0` allow it to detect Patroni promotions; `SHOW REPLICATION` exposes the discovered roles and lag. **PgDog does not elect or fence primaries.** Patroni owns that responsibility. `read_write_split = "prefer_primary"` keeps validation reads on primaries. Transaction pooling avoids pinning backend connections between transactions. The example sets a 16-connection pool per backend, 1-second health checks, a 5-second checkout timeout and a 10-second query timeout; see `config/pgdog.toml`.

Patroni enables `synchronous_mode`, `synchronous_mode_strict`, `synchronous_node_count = 1` and `synchronous_commit = on`. A successful write is flushed on the primary and one eligible synchronous standby. The third copy is asynchronous. If no synchronous standby is available, writes block; do not equate three physical copies with three synchronous acknowledgements. PgDog updates routing after promotion, and Patroni rewinds/rejoins the old primary. `wal_keep_size=512MB` retains recent WAL for rejoin; Patroni may rebuild a diverged replica if rewind fails. This bounds local WAL retention rather than replacing a backup or archive.

## Failure and performance exercises

| Command | Fault and validation |
|---|---|
| `make failover FAULT_SHARD=0` | Kill that shard's current primary with SIGKILL during production, wait for a different primary, restore the old node, reconcile every acknowledged record and verify all nine copies. Repeat with `1` and `2`. |
| `make chaos-replica-lag` | Pause the current synchronous replica for ten seconds during production. Observe commit stalls, uncertain responses and retries; restore it and repeat the same reconciliation. |
| `make test CUSTOMERS=1` | Concentrate writes on one customer to examine a hot shard; use a fresh `make up CUSTOMERS=1` first. Compare with the distributed baseline using the same client settings. |
| `make status` | Per-shard customer/order totals, streaming-replica lag and PgDog's live primary/replica view. |
| `make cli` | SQL through PgDog, including customer-scoped and scatter queries. |

`test` and failure targets truncate only the example's `orders` tables before each run. `make up` creates tables and updates customer rows; use the full cleanup lifecycle before changing the customer count. A run must be large enough to still be active when the fault is injected; the runner rejects a workload that completes before injection.

The Go client uses `database/sql` with pgx. It retries the same composite IDs after errors, including uncertain commit responses. A unique acknowledgement journal on the host mount is synced after every successful batch; failure recovery cannot overwrite an earlier acknowledged batch. `check.json` records missing acknowledged IDs, duplicate IDs, committed rows without a witness and the amount sum. The test fails if any invariant is wrong. Replicas must replay through their primary's captured WAL position and match its count and numeric checksums. `customer-placement.tsv` proves each customer exists on exactly one primary shard.

`load.json` reports elapsed seconds, overall records/s, retry counts, uncertain commit responses and batch p50/p99/**maximum** latency. Phases are assigned when a batch completes; an operation that stalls during a fault may complete in `recovery`. Latencies include retries through the eventual successful commit; journal sync time is included in overall throughput, but follows the per-batch latency measurement. Keep maximum latency: fewer than 1% of requests can stall while p99 remains small. Raw workload errors are in `workload.log` for fault runs.

## Verified test coverage

The final bulk-insert configuration completed the baseline, primary failures on **all three shards**, and a synchronous-replica pause. Each run reconciled **1,000,000 acknowledged and observed orders**, **0 missing acknowledged IDs**, **0 duplicates**, **0 committed rows without a witness**, and **5,000,500,000 cents**; all nine database copies passed catch-up checks.

| Executed scenario | Local run ID |
|---|---|
| Final baseline | `20261114T180959Z` |
| Shard 0 primary failure | `20261114T181035Z` |
| Shard 1 primary failure | `20261114T185129Z` |
| Shard 2 primary failure | `20261114T185240Z` |
| Synchronous-replica pause | `20261114T181227Z` |

The hot-customer (`CUSTOMERS=1`) and two-million-order variants were not run. Earlier row-at-a-time baselines and primary failures were also measured, as described below. The two additional bulk-mode shard failures ran with this example alone on the Docker VM; earlier runs shared it with other examples.

Audit the evidence in the Git-ignored `results/<run-id>/` directories: `load.json`, `check.json`, `acknowledged.jsonl`, `failover.json` where applicable, and `customer-placement.tsv`. Phase latencies below include retries; these runs do not provide a controlled cross-system ranking.

Batch latency includes transaction attempts and retries; it excludes the subsequent journal fsync, which is included in overall elapsed time and throughput.

## Results

Observed on 2026-11-14, Apple M4 Pro, shared aarch64 Docker VM: 11 vCPUs, 24.4 GiB RAM, native ARM64 images, no per-container CPU or memory limits. One run per case, 1,000,000 orders / 10,000 customers / 8 clients / 100 rows per transaction. The baseline, shard-0 failure and replica-pause runs shared the VM with builds and other examples; the two new bulk-mode shard failures used this example alone. These are recovery observations, not capacity estimates.

| Final configuration, `BULK=1` | Elapsed | Orders/s | Retries / commit errors | Promotion or pause | Phase batch p50 / p99 / max |
|---|---:|---:|---:|---:|---:|
| Baseline | 21.477 s | 46,562 | 0 / 0 | — | baseline 8.961 / 139.453 / 876.212 ms |
| Shard 0 primary killed | 95.782 s | 10,440 | 44 / 2 | promotion 20 s | 6.471 / 609.617 / 25,693.009 ms |
| Shard 1 primary killed | 61.622 s | 16,228 | 41 / 0 | promotion 19 s | 1.628 / 33.531 / 21,407.609 ms |
| Shard 2 primary killed | 61.020 s | 16,388 | 32 / 0 | promotion 21 s | 1.438 / 26.011 / 20,452.068 ms |
| Synchronous replica paused | 78.566 s | 12,728 | 8 / 8 | pause 10 s | 279.718 / 10,130.094 / 10,131.739 ms |

Each final run acknowledged and observed **1,000,000 orders**, with **0 missing acknowledged IDs**, **0 duplicates**, **0 committed rows without a witness**, and **5,000,500,000 cents**. All nine copies caught up and matched. The shards held 327,700 / 336,800 / 335,500 orders and 3,277 / 3,368 / 3,355 customers.

Earlier `BULK=0` runs also killed the primary on every shard: shard 0 promoted in 21 s with 43 retries, shard 1 in 19 s with 43 retries, and shard 2 in 21 s with 42 retries. Each reconciled all 1,000,000 orders with zero acknowledged loss and zero duplicates. That row-at-a-time baseline took 20.387 s (49,052 records/s), p50 15.037 ms and p99 31.784 ms. VM contention changed between these runs, so the numbers do not establish a speedup from batching. The final bulk configuration was separately tested on all three shards above.

Phases use transaction completion time: stalled transactions can finish during recovery. Read maximum latency alongside p99; a small number of long stalls can disappear from p99. Fault targets pace offered work at `CHAOS_RATE=25000`, leaving the producer active through promotion or replica pause.

## Limits and known issues

- Creating schema directly on the shards initially left PgDog serving scatter queries from one shard. A configuration reload loaded the new tables and restored scatter routing; provisioning now includes that reload. The acknowledgement verifier caught the initial incomplete query result.
- All services share one host; these tests exercise process loss and a paused replica, not independent disk, host or network failure domains. A single PgDog endpoint remains a router availability dependency.
- Routing by a customer key does not split one large customer's write load across shards. Cross-shard transactions, replica reads, network partitions and full-quorum loss have different failure modes and are not established by these results.
- A host-mounted client journal survives the database faults injected here. It is not a claim about surviving loss of the host itself.

## References

- [PgDog sharded-table configuration](https://docs.pgdog.dev/configuration/pgdog.toml/sharded_tables/)
- [PgDog replication, automatic role discovery and failover boundaries](https://docs.pgdog.dev/features/load-balancer/replication-failover/)
- [PgDog 0.1.62 configuration source](https://github.com/pgdogdev/pgdog/blob/v0.1.62/pgdog-config/src/general.rs)
- [PgDog 0.1.62 shard routing source](https://github.com/pgdogdev/pgdog/tree/v0.1.62/pgdog/src/frontend/router/sharding)
- [Patroni synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html)
- [Patroni 4.0.6 election and synchronous replication implementation](https://github.com/patroni/patroni/blob/v4.0.6/patroni/ha.py)
