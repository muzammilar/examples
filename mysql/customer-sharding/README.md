# MySQL customer sharding with Vitess

Three customer shards, each with one MySQL primary and two replicas. VTGate routes customer-local transactions; VTOrc promotes a replica while the Go producer keeps writing.

## Quick start

1. `make docker-up` — start etcd, VTGate, VTctld and nine MySQL tablets; wait for MySQL readiness.
2. `make up` — provision shards and VSchema, start VTOrc, build the client and seed 10,000 customers.
3. `make test` — write 1,000,000 orders, reconcile every ID and verify all replicas.
4. `make down` — drop the example tables.
5. `make docker-down` — delete this project's containers, named volumes and locally built client image.

Requires Docker Compose v2, Make and Python 3 for control-plane JSON parsing. Allow at least 12 GiB free Docker disk space. The pinned Vitess 23.0.0 image is amd64 and runs under emulation on Apple Silicon. VTGate binds `127.0.0.1:15306` (`VITESS_PORT` overrides it). The unauthenticated local endpoint is for this example only.

## Placement and durability

| Component | Configuration |
|---|---|
| Routing | Vitess 23.0.0 VTGate, `hash` vindex on `customer_id` for both tables |
| Shards | `-55`, `55-aa`, `aa-`; 3 tablets each, 9 MySQL copies total |
| Replication | Vitess keyspace `semi_sync` durability; GTID replication |
| MySQL | 64 MiB buffer pool per tablet, `innodb_flush_log_at_trx_commit=1`, `sync_binlog=1`, `sync_relay_log=1` |
| Coordination | etcd 3.5.21, three members; VTctld and VTOrc |
| Workload | Go `database/sql`, go-sql-driver/mysql 1.9.3; 8 clients, 100 rows per customer-local transaction |

Each customer and its orders use the same vindex, so a write batch goes to one shard. Placement checks read the primaries directly and prove that all 10,000 customer IDs occur on exactly one shard. The verifier streams the join through VTGate using OLAP workload mode, rather than raising its OLTP row limit.

`results/<RUN_ID>/acknowledged.jsonl` is an external, fsynced witness of successful transactions. Retryable failures reuse deterministic IDs and unique keys. Reconciliation checks every ID, expected customer, amount and duplicate; it reports committed rows without a client witness separately. Replication checks capture each primary's GTID position, wait for both replicas to execute it, and compare row counts and sums. An incomplete load or any missing acknowledged ID fails the target.

Semi-sync acknowledges replication to another tablet; it is not a guarantee against losing the host or all replicas. All nine copies share this Docker VM's physical storage. Durable application acknowledgements are measured against the external journal, not inferred from a process being healthy.

## Failure and workload commands

| Command | Action |
|---|---|
| `make failover FAULT_SHARD=-55` | SIGKILL the current primary, wait for a different primary, restore the old tablet and verify catch-up |
| `make failover FAULT_SHARD=55-aa` | Repeat on the second shard |
| `make failover FAULT_SHARD=aa-` | Repeat on the third shard |
| `make test COUNT=2000000` | Two-million-order correctness run |
| `make test BULK=0` | One INSERT per row inside each 100-row transaction |
| `make status` / `make cli` | Tablet topology / SQL shell |

Defaults: `COUNT=1000000 CUSTOMERS=10000 WORKERS=8 BATCH=100 BULK=1 RATE=0`. Fault targets use `CHAOS_RATE=25000` records/s as the dispatch ceiling to keep traffic active during recovery. The producer is never paused for failure injection. Each test resets orders and requires a fresh `RUN_ID`.

This example keeps three shards fixed. Online `Reshard` workflows and topology expansion are not implemented here; adding a tablet is different from adding a customer shard.

## Verified test coverage

The following runs completed live against Docker deployments. Each reconciled **1,000,000 acknowledged and observed orders**, **0 missing acknowledged IDs**, **0 duplicate IDs**, **0 committed rows without a witness**, and **5,000,500,000 cents**. All configured replicas passed the example's catch-up checks.

| Executed scenario | Local run ID |
|---|---|
| Baseline | `20261114T172747Z` |
| Shard -55 primary failure | `20261114T173027Z` |
| Shard 55-aa primary failure | `20261114T173347Z` |
| Shard aa- primary failure | `20261114T173503Z` |
| Fresh-lifecycle baseline | `20261114T182427Z` |

The two-million-order variant was not run. A row-at-a-time attempt was cancelled before completion and is not a passing benchmark.

Audit the raw evidence in `results/<run-id>/`: `load.json`, `check.json`, `acknowledged.jsonl` and, for fault/scaling runs, `failover.json`. Journals and raw reports remain local and are ignored by Git. Timing, phase latency, offered-rate limits and shared-VM conditions are detailed below; these runs do not provide a controlled cross-system ranking.

Batch latency includes transaction attempts and retries; it excludes the subsequent journal fsync, which is included in overall elapsed time and throughput.

## Measured results

2026-11-14, Apple M4 Pro, aarch64 Docker VM with 11 vCPUs / 24.4 GiB RAM, shared with other examples; Vitess amd64 emulated, Go client native. No per-container CPU cap. One million orders / 10,000 customers / 8 clients / 100 rows per transaction, one run per scenario. Latencies include retries and use transaction completion phase.

| Run | Elapsed | Orders/s | Primary promotion | Retries / commit errors | Phase batch p50 / p99 / max |
|---|---:|---:|---:|---:|---:|
| Baseline | 29.523 s | 33,872 | — | 0 / 0 | baseline 21.476 / 59.319 / 96.493 ms |
| Shard `-55` failure | 57.935 s | 17,261 | 4 s | 234 / 1 | 9.440 / 105.226 / 3,132.128 ms |
| Shard `55-aa` failure | 66.210 s | 15,103 | 3 s | 232 / 1 | 12.250 / 104.039 / 3,094.284 ms |
| Shard `aa-` failure | 88.787 s | 11,263 | 5 s | 263 / 0 | 11.185 / 87.340 / 3,527.919 ms |

All four runs acknowledged and observed **1,000,000 orders**, with **0 lost acknowledged IDs**, **0 duplicates**, **0 rows without a client witness**, and an amount sum of **5,000,500,000 cents**. All nine copies caught up to the captured GTID positions. These measurements include shared-VM contention and emulation; they are not capacity estimates.

A final clean-storage lifecycle with the stronger MySQL socket health check and explicit VTGate query readiness also passed: 1,000,000 orders in 132.563 s (7,544 orders/s), p50 50.524 ms, p99 1,288.694 ms, maximum 2,124.071 ms, 0 retries and 0 acknowledged loss. Shard totals were 335,300 / 333,400 / 331,300 orders and all nine copies matched. Citus rebalancing shared the VM during this run; the difference from the earlier baseline is another reason not to infer product capacity from these runs.

## Design references

- [Vitess durability policy](https://vitess.io/docs/23.0/user-guides/configuration-basic/durability_policy/): promotion and semi-sync acknowledgement rules.
- [VSchema and vindexes](https://vitess.io/docs/23.0/reference/features/vschema/): routing customer data by a common distribution key.
- [Vitess 23 tablet startup source](https://github.com/vitessio/vitess/blob/v23.0.0/examples/common/scripts/vttablet-up.sh): mysqlctld/vttablet lifecycle.
- [Vitess 23 reparenting source](https://github.com/vitessio/vitess/tree/v23.0.0/go/vt/vtctl/reparentutil): replica eligibility, promotion and repair paths.

Compare NDB's shared distributed storage engine with these independent MySQL shards in the NDB example. The systems expose different transaction and recovery mechanisms even though both configurations retain three copies of customer data.
