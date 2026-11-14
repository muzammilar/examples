# PgDog pooling and primary discovery

PgDog 0.1.62 in front of one PostgreSQL primary and two replicas. This example isolates transaction pooling, explicit replica reads and Patroni failover. Customer sharding is a separate example.

## Quick start

1. `make docker-up` — render PgDog configuration, build Patroni and wait for three PostgreSQL copies and etcd.
2. `make up` — create the tables, reload PgDog, build the Go client and seed 10,000 customers.
3. `make test` — insert/reconcile 1,000,000 orders, compare direct/pooled connection bursts and check both replicas caught up.
4. `make down` — drop the example tables.
5. `make docker-down` — remove this Compose project's services, named volumes and locally built images.

Requires Docker Compose v2, Make and Python 3.11+ (`tomllib` validates rendered configuration). SQL binds `127.0.0.1:6433`, overridable with `PGDOG_PORT`. Local passwords are in the example files. Allow at least 4 GiB free Docker disk space.

## Configuration experiments

| Setting | Value / behavior |
|---|---|
| PgDog | 0.1.62; `pooler_mode="transaction"`, query parser enabled |
| Pool | `POOL_SIZE=16` connections per backend, minimum 1 |
| Discovery | Three database entries with `role="auto"`, `lsn_check_delay=0`, 1 s health checks |
| Reads | `read_write_split="prefer_primary"`; explicit replica session for the replica-read test |
| Timeouts | Connect 1 s, checkout 5 s, query 10 s |
| PostgreSQL / Patroni | PostgreSQL 17.6, Patroni 4.0.6, etcd 3.5.21 with three members |
| Durability | Strict synchronous mode, one synchronous standby, synchronous commit on; third copy asynchronous |
| Recovery | Checksums, pg_rewind, 512 MiB retained WAL; rebuild a diverged replica if rewind fails |

PgDog routes connections and discovers roles; Patroni elects and rejoins primaries. A successful write waits for the primary and one synchronous standby, rather than all three copies. Transaction pooling releases backend connections between transactions. Larger pools can increase concurrency and server contention; measure them for the actual workload.

| Command | Experiment |
|---|---|
| `make config` | Inspect live `SHOW CONFIG`, `SHOW POOLS` and `SHOW REPLICATION` |
| `make pool-size POOL_SIZE=4` | Render validated TOML and issue `RELOAD` through the admin database |
| `make compare-pools` | Run 1,000,000 orders each with pool sizes 4 and 16, 64 concurrent clients; restore pool 16 |
| `make replica-reads` | Set `pgdog.role='replica'` and assert `pg_is_in_recovery()` is true |
| `make failover` | SIGKILL the primary while writing; wait for another primary, restore the old node, reconcile IDs and verify all copies |
| `make chaos-replica-lag` | Pause the synchronous replica for 10 s while writing; reconcile after resume |
| `make test COUNT=2000000` | Two-million-order run |
| `make test BULK=0` | Row-at-a-time INSERT comparison within each transaction |
| `make status` / `make cli` | Replication status / SQL shell through PgDog |

The default workload is `COUNT=1000000 CUSTOMERS=10000 WORKERS=8 BATCH=100 BULK=1 RATE=0`. Failure tests use an offered-rate ceiling of `CHAOS_RATE=25000` records/s to span recovery. They never pause the producer. Each test truncates orders and writes an exclusive, fresh `results/<RUN_ID>/` journal. Configure the customer count on `make up`; use a clean lifecycle when changing it.

The Docker-built Go client uses `database/sql` and pgx 5.7.6. Batches contain deterministic customer-local IDs, and retrying uncertain commits uses `ON CONFLICT DO NOTHING`. Every successful batch is appended and fsynced outside the database containers. Verification checks every joined ID/customer/amount, missing acknowledged IDs, duplicate IDs and rows committed without a journal witness. Replicas must replay through the captured primary WAL position and match counts and numeric checksums. An incomplete load fails.

## Representative workload: short-lived connection bursts

`make test` now compares a burst of serverless-style customer dashboard requests against direct PostgreSQL and PgDog. After the million-order data is loaded, `make connection-burst` reruns it. Each request opens a new Go `database/sql` connection, reads the customer's order total, checks the deterministic result and closes the connection. Client-side reuse is disabled so it cannot hide connection churn.

The test uses **4,000 requests and 256 concurrent clients**, the same SQL and data for both paths, PostgreSQL `max_connections=100` and PgDog transaction pool size 16. Direct PostgreSQL capacity errors (`53300`) are counted without retrying; another error or an incorrect result fails the test. PgDog must serve all requests. A pre-established monitor samples primary client-backend counts every 20 ms; these are observed samples, not an exact peak guarantee.

2026-11-14, Apple M4 Pro, shared native aarch64 Docker VM, 11 vCPUs / 24.4 GiB RAM, no CPU caps, one run, 1,000,000 orders / 10,000 customers. Run `20261114T191311Z`.

| Path | Successful / attempted | Capacity errors | Elapsed | Successful requests/s | Successful p50 / p99 / max | Sampled primary backends |
|---|---:|---:|---:|---:|---:|---:|
| Direct PostgreSQL | 2,520 / 4,000 | 1,480 | 2.209 s | 1,141 | 79.614 / 1,045.717 / 1,098.779 ms | 70 |
| PgDog transaction pooling | 4,000 / 4,000 | 0 | 1.145 s | 3,494 | 72.449 / 116.582 / 131.053 ms | 19 |

Successful throughput was 3.06× higher through PgDog in this burst. Latency includes connection setup, query and close; its percentiles include successful requests only. Direct PostgreSQL rejected 37% of attempts, so comparing latency alone would hide a material part of the result. The workload demonstrates bounded backend reuse and admission under connection churn. Pooling does not make a slow query cheaper or eliminate checkout timeouts under sustained overload. Transaction pooling also requires care with session state.

`BURST_CLIENTS=256` and `BURST_REQUESTS=4000` are configurable Make variables. `results/<run-id>/connection-burst.json` records both paths, error SQLSTATEs and sampled backend counts. The full test and cleanup lifecycle passed.

Sources: [PgDog transaction-pool settings](https://docs.pgdog.dev/configuration/pgdog.toml/general/), [0.1.62 pool creation limits and waiter handoff](https://github.com/pgdogdev/pgdog/blob/v0.1.62/pgdog/src/backend/pool/inner.rs). The capacity failures, counts and timings above were measured locally.

## Verified test coverage

The following runs completed live against Docker deployments. Each reconciled **1,000,000 acknowledged and observed orders**, **0 missing acknowledged IDs**, **0 duplicate IDs**, **0 committed rows without a witness**, and **5,000,500,000 cents**. All configured replicas passed the example's catch-up checks.

| Executed scenario | Local run ID |
|---|---|
| Final baseline | `20261114T181709Z` |
| Primary failure | `20261114T181747Z` |
| Synchronous-replica pause | `20261114T182805Z` |
| Pool 4, 64 clients | `20261114T174443Z-pool4` |
| Pool 16, 64 clients | `20261114T174443Z-pool16` |

The explicit replica-routing assertion also passed. The two-million-order and row-at-a-time comparison variants were not run.

Audit the raw evidence in `results/<run-id>/`: `load.json`, `check.json`, `acknowledged.jsonl` and, for fault/scaling runs, `failover.json`. Journals and raw reports remain local and are ignored by Git. Timing, phase latency, offered-rate limits and shared-VM conditions are detailed below; these runs do not provide a controlled cross-system ranking.

Batch latency includes transaction attempts and retries; it excludes the subsequent journal fsync, which is included in overall elapsed time and throughput.

## Measured results

2026-11-14, Apple M4 Pro, shared native aarch64 Docker VM, 11 vCPUs / 24.4 GiB RAM; no per-container CPU cap. One run per case, 1,000,000 orders / 10,000 customers / 100 rows per transaction. Other examples and builds shared the VM; these are local recovery and configuration measurements, not capacity estimates.

| Pool experiment | Clients | Elapsed | Orders/s | Batch p50 / p99 / max |
|---|---:|---:|---:|---:|
| Pool 4 | 64 | 3.566 s | 280,400 | 19.245 / 65.423 / 117.137 ms |
| Pool 16 | 64 | 3.533 s | 283,082 | 14.932 / 67.226 / 90.863 ms |

Pool 16 produced 1.01× the observed throughput of pool 4, with p99 67.226 ms versus 65.423 ms. This short run provides no evidence of a meaningful throughput gain from the larger pool.

The earlier baseline with 8 clients took 4.290 s (233,118 orders/s), p50 2.343 ms and p99 31.551 ms. Primary loss promoted a replacement in 21 s, with 36 retried attempts and 1 uncertain commit response; failure-phase maximum batch latency was 20,554.177 ms. Every completed run reconciled **1,000,000 acknowledged / observed orders**, **0 missing acknowledged IDs**, **0 duplicates** and **5,000,500,000 cents**. The explicit replica-read assertion passed. The final 512 MiB retained-WAL configuration was then tested from fresh volumes:

| Final configuration | Elapsed | Orders/s | Retries / commit errors | Batch p50 / p99 / max |
|---|---:|---:|---:|---:|
| Baseline, 8 clients | 27.267 s | 36,675 | 0 / 0 | 11.142 / 207.830 / 1,029.230 ms |
| Primary failure | 81.321 s | 12,297 | 47 / 4 | failure phase 6.800 / 803.358 / 25,701.363 ms |

The final primary promotion took 21 s. Both final runs again reconciled 1,000,000 orders, 0 lost acknowledged IDs, 0 duplicates and 5,000,500,000 cents, and all three copies caught up. Explicit replica routing passed again. The large rate difference from the earlier measurements demonstrates shared-VM contention; it does not isolate a WAL-retention performance effect. The separate synchronous-replica pause test also passed: 51.627 s elapsed, 19,370 orders/s, 10 s paused, 8 total retries and 8 uncertain commit responses across pause/recovery phases, maximum batch latency 10,115.117 ms. All 1,000,000 orders remained, with 0 acknowledged loss, 0 duplicates and 5,000,500,000 cents; all three copies caught up.

## Limits and references

One PgDog endpoint and one physical host remain availability dependencies. Replica reads are eventually consistent unless the application supplies its own consistency protocol. The explicit replica-read check verifies routing, not read-your-writes semantics. This example does not shard data or test online resharding; see the separate customer-sharding example for three shards.

- [PgDog general configuration](https://docs.pgdog.dev/configuration/pgdog.toml/general/).
- [PgDog automatic role discovery and failover](https://docs.pgdog.dev/features/load-balancer/replication-failover/).
- [PgDog 0.1.62 configuration source](https://github.com/pgdogdev/pgdog/blob/v0.1.62/pgdog-config/src/general.rs).
- [Patroni synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html) and [4.0.6 election implementation](https://github.com/patroni/patroni/blob/v4.0.6/patroni/ha.py).
