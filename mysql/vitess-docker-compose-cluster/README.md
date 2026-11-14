# Vitess deployment and primary recovery

One unsharded Vitess keyspace with one MySQL primary and two replicas. This deployment example focuses on bootstrap, semi-sync durability, VTGate routing and VTOrc recovery. The separate customer-sharding example uses three shards.

## Quick start

1. `make docker-up` — start three etcd members, VTctld, VTGate and three tablets; wait for MySQL and tablet HTTP readiness.
2. `make up` — provision keyspace/shard and schema, start VTOrc, build the Go client and seed 10,000 customers.
3. `make test` — write and reconcile 1,000,000 orders, then wait for both replicas to execute the primary's GTID position.
4. `make down` — drop the example tables.
5. `make docker-down` — remove this Compose project's services, volumes and locally built client image.

Requires Docker Compose v2, Make and Python 3 for control-plane JSON parsing. Allow at least 5 GiB free Docker disk space. The official Vitess 23.0.0 image is amd64 and requires emulation on Apple Silicon. SQL binds `127.0.0.1:15307`, overridable with `VITESS_PORT`. This local endpoint has no authentication.

## Setup and checks

| Component | Configuration |
|---|---|
| Vitess | `vitess/lite:v23.0.0`; VTGate, VTctld, VTOrc |
| Tablets | `t100`, `t101`, `t102`, each running mysqlctld and vttablet with supervised shutdown |
| Topology | One cell `zone1`, keyspace `orders`, unsharded shard `0` |
| Replication | Keyspace durability `semi_sync`; MySQL GTIDs |
| MySQL storage | Named volume per tablet, 64 MiB buffer pool, commit/binlog/relay-log sync settings enabled |
| Coordination | Three etcd 3.5.21 members |
| Client | Go modules, `database/sql`, go-sql-driver/mysql 1.9.3; Docker-built nonroot binary |

Bootstrap uses `PlannedReparentShard` for an uninitialized shard and applies schema and VSchema through VTctld. It does not force-initialize a previously populated primary. Tablet readiness requires a working local MySQL socket, since the vttablet HTTP endpoint can become available before MySQL has initialized.

The workload writes deterministic customer-local batches and fsyncs each successful batch to `results/<RUN_ID>/acknowledged.jsonl` outside the database containers. Retried ambiguous commits reuse unique IDs. Verification streams every joined order through VTGate in OLAP mode, checks IDs/customer/amount, and compares with the journal. It reports missing acknowledged IDs, duplicates and committed rows without a journal witness. Direct replica checks wait for the captured primary GTID and compare counts and numeric sums.

## Failure and workload commands

| Command | Action |
|---|---|
| `make failover` | SIGKILL the current primary under active traffic, wait for VTOrc promotion, restart the old tablet and verify all three copies |
| `make test COUNT=2000000` | Two-million-order run |
| `make test BULK=0` | One INSERT per row within a transaction for a round-trip comparison |
| `make status` | Tablet types and Compose health |
| `make cli` | MySQL shell through VTGate |

Defaults: `COUNT=1000000 CUSTOMERS=10000 WORKERS=8 BATCH=100 BULK=1 RATE=0`. `CHAOS_RATE=25000` is the offered records/s ceiling during fault tests. Producers continue through the fault. A test resets orders and requires a fresh result directory; an incomplete run or failed reconciliation exits nonzero. Run a clean lifecycle when changing customer count.

## Verified test coverage

The following runs completed live against Docker deployments. Each reconciled **1,000,000 acknowledged and observed orders**, **0 missing acknowledged IDs**, **0 duplicate IDs**, **0 committed rows without a witness**, and **5,000,500,000 cents**. All configured replicas passed the example's catch-up checks.

| Executed scenario | Local run ID |
|---|---|
| Baseline | `20261114T181042Z` |
| Primary failure | `20261114T181521Z` |

The two-million-order and row-at-a-time comparison variants were not run.

Audit the raw evidence in `results/<run-id>/`: `load.json`, `check.json`, `acknowledged.jsonl` and, for fault/scaling runs, `failover.json`. Journals and raw reports remain local and are ignored by Git. Timing, phase latency, offered-rate limits and shared-VM conditions are detailed below; these runs do not provide a controlled cross-system ranking.

Batch latency includes transaction attempts and retries; it excludes the subsequent journal fsync, which is included in overall elapsed time and throughput.

## Measured results

2026-11-14, Apple M4 Pro, shared aarch64 Docker VM with 11 vCPUs / 24.4 GiB RAM. Vitess/MySQL amd64 emulated, Go client native, no CPU caps. One million orders / 10,000 customers / 8 clients / 100 rows per transaction, one run per case. Other examples and builds shared the VM.

| Scenario | Elapsed | Orders/s | Retries / commit errors | Batch p50 / p99 / max |
|---|---:|---:|---:|---:|
| Baseline | 269.770 s | 3,707 | 0 / 0 | 119.788 / 1,592.595 / 4,251.935 ms |
| Primary failure | 237.291 s | 4,214 | 280 / 8 | failure phase 35.326 / 174.325 / 3,934.753 ms |

VTOrc promoted `t102` after killing `t100` in **4 s**. Both runs acknowledged and observed **1,000,000 orders**, lost **0 acknowledged IDs**, found **0 duplicates** and **0 rows without a witness**, and reconciled **5,000,500,000 cents**. All three copies caught up and matched. Latencies include retries and use the phase when a transaction completed; recovery-phase p99 in the fault run was 1,603.344 ms. The changing shared-VM contention prevents treating the higher fault-run rate as an improvement.

## Limits and sources

This example fixes one shard and three tablets; online resharding and replica expansion are not implemented. Semi-sync needs another replica acknowledgement, not three synchronous copies. All containers share one disk and host; host loss, storage loss, network partitions, VTGate HA and etcd quorum loss are not established by these process-failure tests.

- [Vitess 23 durability policy](https://vitess.io/docs/23.0/user-guides/configuration-basic/durability_policy/).
- [Vitess 23 tablet startup source](https://github.com/vitessio/vitess/blob/v23.0.0/examples/common/scripts/vttablet-up.sh).
- [Vitess 23 reparenting implementation](https://github.com/vitessio/vitess/tree/v23.0.0/go/vt/vtctl/reparentutil).
- [Vitess 23 VTOrc source](https://github.com/vitessio/vitess/tree/v23.0.0/go/vt/vtorc).
