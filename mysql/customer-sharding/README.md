# MySQL customer sharding with Vitess

Three customer shards, each with one MySQL primary and two replicas. VTGate routes customer-local transactions; VTOrc promotes a replica while the Go producer keeps writing.

## Quick start

1. `make docker-up` — start etcd, VTGate, VTctld and nine MySQL tablets; wait for MySQL readiness.
2. `make up` — provision shards and VSchema, start VTOrc, build the Go workload and seed 10,000 customers.
3. `make test` — write 1,000,000 orders, reconcile every ID and verify all replicas.
4. `make down` — drop the example tables.
5. `make docker-down` — delete this project's containers, named volumes and locally built workload image.

Requires Docker Compose v2, Make and Python 3 for the test controller. Allow at least 12 GiB free Docker disk space; retained histories and MySQL logs grow across repeated runs. The pinned Vitess 23.0.0 image is amd64 and runs under emulation on Apple Silicon. VTGate binds `127.0.0.1:15306` (`VITESS_PORT` overrides it). The unauthenticated local endpoint is for this example only.

## Placement and durability

| Component | Configuration |
|---|---|
| Routing | Vitess 23.0.0 VTGate, `hash` vindex on `customer_id` for both tables |
| Shards | `-55`, `55-aa`, `aa-`; 3 tablets each, 9 MySQL copies total |
| Replication | Vitess keyspace `semi_sync` durability; GTID replication |
| Resource limits | Each tablet 1 CPU / 1 GiB; each etcd 0.25 CPU / 256 MiB; VTctld and VTOrc each 0.5 CPU / 512 MiB; VTGate 1 CPU / 1 GiB; each workload process 2 CPU / 1 GiB |
| MySQL | Observed 8.4.6, 64 MiB buffer pool per tablet, `innodb_flush_log_at_trx_commit=1`, `sync_binlog=1`, `sync_relay_log=1` |
| Coordination | etcd 3.5.21, three members; VTctld and VTOrc |
| Workload | Go `database/sql`, go-sql-driver/mysql 1.9.3; 8 clients, 100 rows per customer-local transaction |

Each customer and its orders use the same vindex, so each write batch goes to
one shard. Setup queries Vitess's native `customer_hash` vindex function for all
10,000 keyspace IDs and compares them with the three native shard ranges. It
records an external fixture UUID and all nine MySQL server UUIDs; repeated setup
preserves data and refuses legacy tables without their witness.

`orders-workload/` contains the local Go writer, independent reader and verifier;
Compose builds them with formatting, vet and unit-test checks. `control.py` performs
native operations and `report.py` records phase results. Nothing depends on a
sibling example. Generated journals, checks and images are excluded from Git.

Every run reserves disjoint operation IDs and appends fsynced attempt/ACK/error
journals under `results/<fixture-id>/runs/<run-id>/`. No test truncates earlier
orders. Final reconciliation scans every historical order body and all customer
names through VTGate, then directly on **every one of the nine MySQL copies**.
It detects missing ACKs, wrong values, wrong physical shard, duplicate IDs,
unattempted rows and partial transactions; complete ambiguous commits are counted
separately. Direct verifiers run as the pinned image's non-root MySQL UID 999,
using its Unix socket through a read-only mount of that exact owned volume.
VTGate scans pin one connection in OLAP mode rather than raising the OLTP row cap.

The independent reader probes shards serially, waiting 100 ms between requests
with a 3 s request timeout. It samples immutable prior ACK bodies (16 per shard), falling
back to seeded customer names on a fresh fixture. It observes availability while
the writer blocks/retries. It uses PRIMARY routing; fresh-write visibility lag
and continuous replica stale-read behavior are not measured here.
Each fault requires affected-shard read/write progress before injection and after
recovery. Primary and replica crashes also require progress while the victim stays
down. Readers continue during final full-data verification.

Native repair checks capture a GTID barrier per shard and wait for both replicas
to execute it. All nine tablets must retain their server/fixture UUIDs, correct
read-only/topology roles, both replica threads and source UUID, durable MySQL
settings and `SERVING` tablet query services. Semi-sync must be ON with at least
one acknowledgement peer. The measured native configuration waits for **one**
replica, `AFTER_SYNC`, with `wait_no_replica=ON` and timeout
`1000000000000000000` ms; the controller verifies these settings. Three copies do
not mean three synchronous acknowledgements. Total replica/quorum loss is an
unrun case; successful isolated failures do not establish its availability.

## Failure and workload commands

| Command | Action |
|---|---|
| `make test-production` | Serial baseline, three primary crashes, three replica pauses, one replica crash, router crash and final historical verification |
| `make failover FAULT_SHARD=-55` | Kill the current shard primary, prove exit 137, await a different primary and affected-shard progress while the old primary is still down, then restore it |
| `make replica-pause FAULT_SHARD=55-aa` | Pause one actual replica for 10 s; resume and prove GTID/thread/query-service recovery |
| `make replica-crash FAULT_SHARD=aa-` | Kill a non-primary tablet, require degraded progress, retain its volume and repair |
| `make router-failure` | Kill VTGate for 10 s; observe failed requests, restore it and prove all-shard recovery |
| `make test COUNT=2000000` | Append a two-million-order correctness history |
| `make benchmark` | Append another uncapped-dispatch workload; retains all earlier data |
| `make verify` | Full historical body/placement checks through VTGate and on all nine native copies |
| `make unit-test` | Docker build checks plus controller/report negative controls |
| `make status` / `make cli` | Native tablet topology / SQL shell |

Defaults: `COUNT=1000000 CUSTOMERS=10000 WORKERS=8 BATCH=100`.
`CHAOS_RATE=25000` is the live fault workload's dispatch ceiling, not a capacity
claim. Live runs reserve `MAX_ROWS=10*COUNT`, keep writing throughout native repair
and stop only after the controller requests it **and** the minimum count is met.
Failed attempts and their journals remain evidence; resume against the same
fixture rather than resetting it. The controller checks container/start/restart
identity for all 14 unrelated surviving services and mysqld PID/start time for the
unaffected tablets. A changed survivor fails the case.

Three shards stay fixed. Online `Reshard`, more/fewer tablets, graceful/rolling
maintenance, version upgrades, resource changes, topology/quorum loss, peer
partitions, stale-primary rejection, write-I/O failure/permanent disk loss,
pressure, backup/restore, single-row ingestion and controlled cold/warm benchmarks
remain separate, **unrun** cases. Killing a container with its volume retained is
not a disk-failure test. All copies, etcd members and journals share one host;
this does not establish independent-host or power-loss durability. Local SQL and
control endpoints are unauthenticated; this is not a production security setup.

## Measured results

2026-11-16, Apple M4 Pro, Docker 29.5.3 aarch64 VM, **11 vCPUs / 24.4 GiB**;
Vitess/MySQL amd64 emulated, Go workload native. Effective limits are recorded in
`resources.json`: 15 long-running infrastructure services capped at an aggregate
11.75 CPU / 11.75 GiB; two concurrent workload processes add 4 CPU / 2 GiB.
These are shared CPU quotas, not dedicated cores. One run per case, 10,000
customers, eight writer connections, 100-row customer-local transactions. The
live fault workload is rate limited to at most 25,000 rows/s; datasets grow between
cases. These timings are not a capacity estimate or a fair product comparison.

Fixture `7d1665a6-be3f-4212-a306-e5b992dc651a` reconciled
**10,252,200 ACK orders across 10 histories**, plus all 10,000 customers.
Every completed case checked VTGate and all nine native copies: **0 missing ACKs,
wrong values, misplaced rows, duplicate IDs, unattempted rows, partial transactions
or committed-without-ACK rows**. Independent observers recorded **0 wrong/missing
reads**. Failed requests remain visible and are counted separately from successful
requests. Raw evidence stays in ignored `results/`, including phase-specific
success/error latency and throughput, per-shard progress and survivor identities.

| Case | New ACK orders | Writer elapsed s | Orders/s | Failed attempts / ambiguous commits | Promotion s | Native repair barrier s | Longest read-success gap s |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 1,000,000 | 32.300 | 30,959 | 0 / 0 | — | — | 0.331 |
| Primary crash `-55` | 1,174,600 | 52.418 | 22,408 | 300 / 3 | 4.826 | 17.020 | 4.024 |
| Primary crash `55-aa` | 1,000,000 | 60.333 | 16,575 | 319 / 0 | 4.762 | 22.335 | 4.328 |
| Primary crash `aa-` | 1,000,000 | 60.235 | 16,602 | 185 / 0 | 3.452 | 26.028 | 2.803 |
| Replica pause `-55` | 1,000,000 | 40.820 | 24,498 | 0 / 0 | — | 15.925 | 0.360 |
| Replica pause `55-aa` | 1,000,000 | 62.421 | 16,020 | 0 / 0 | — | 13.635 | 0.575 |
| Replica pause `aa-` | 1,000,000 | 41.086 | 24,339 | 0 / 0 | — | 13.368 | 0.434 |
| Replica crash `-55` | 1,077,600 | 71.716 | 15,026 | 0 / 0 | — | 39.815 | 0.834 |
| VTGate crash | 1,000,000 | 51.636 | 19,366 | 729 / 3 | — | 13.386 | 11.588 |
| Post-recovery benchmark | 1,000,000 | 33.171 | 30,147 | 0 / 0 | — | — | 0.376 |

Promotion includes the SIGKILL RPC and 1 s topology polling. Repair timing begins
after Compose has restored the victim's MySQL health and includes the complete
nine-copy GTID/role/query-service barrier under continuing load. Crashes hold the
victim down for 10 s after the required degraded progress (VTGate uses a fixed
10 s outage); pauses hold for 10 s. Read gaps include the serial three-shard probe
cadence and request timeouts, not just server election time. The complete case
also includes pre/post verification and is longer than writer elapsed time.

Request latency below is nearest-rank **p50 / p99 ms (sample count)**, grouped by
completion time and outcome across the case. Write-success latency is the
successful transaction attempt, excluding journal fsync and preceding retries;
raw phase reports separately retain batch completion latency including retries.
Writer throughput includes its journal fsync. Error latencies never enter the
successful-request percentile. Phases and start/completion timestamps are retained.

| Case | Write success | Write errors | Read success | Read errors |
|---|---:|---:|---:|---:|
| Baseline | 20.798 / 87.847 (10,000) | — | 1.291 / 4.584 (530) | — |
| Primary crash `-55` | 9.964 / 66.316 (11,746) | 1.995 / 9.800 (300) | 1.288 / 4.981 (767) | 0.497 / 1.118 (12) |
| Primary crash `55-aa` | 11.812 / 193.498 (10,000) | 1.693 / 6.270 (319) | 1.269 / 4.238 (830) | 0.434 / 0.582 (13) |
| Primary crash `aa-` | 11.572 / 218.231 (10,000) | 1.721 / 9.618 (185) | 1.253 / 5.843 (859) | 0.443 / 0.754 (8) |
| Replica pause `-55` | 9.263 / 55.671 (10,000) | — | 1.292 / 4.365 (704) | — |
| Replica pause `55-aa` | 17.438 / 255.617 (10,000) | — | 1.274 / 4.285 (910) | — |
| Replica pause `aa-` | 8.969 / 60.336 (10,000) | — | 1.308 / 6.106 (735) | — |
| Replica crash `-55` | 18.149 / 400.096 (10,776) | — | 1.260 / 4.980 (1,083) | — |
| VTGate crash | 8.954 / 44.074 (10,000) | 5.356 / 19.406 (729) | 1.330 / 8.602 (770) | 8.068 / 17.030 (102) |
| Post-recovery benchmark | 20.698 / 93.086 (10,000) | — | 1.342 / 6.364 (710) | — |

| Case | Local run ID |
|---|---|
| Baseline | `20261116T080216-7dd55de4` |
| Primary crash `-55` | `20261116T080336-cf967198` |
| Primary crash `55-aa` | `20261116T080525-073d33f9` |
| Primary crash `aa-` | `20261116T080723-d2bcdce9` |
| Replica pause `-55` | `20261116T080927-a5b280e9` |
| Replica pause `55-aa` | `20261116T081114-fc7e737d` |
| Replica pause `aa-` | `20261116T081325-d4d57aa8` |
| Replica crash `-55` | `20261116T081519-4d1cb245` |
| VTGate crash | `20261116T081754-6e502bcd` |
| Post-recovery benchmark | `20261116T082058-bea5edf0` |

## Fresh-volume lifecycle

After the ten-history suite, repeated `make up`, `make status`, `make unit-test`,
`make down` and `make docker-down` passed. Cleanup verified zero owned containers,
volumes, networks and built workload tags. The complete five-command lifecycle
then recreated all volumes and passed another **1,000,000 ACK orders** on fixture
`dcd8bef5-41c5-49ae-abd2-aa9d3b62f361`, run
`20261116T082635-fb7d4b66`: **28.566 s / 35,007 orders/s**, successful transaction
p50/p99 **19.609 / 53.130 ms**, **0 retries, ambiguous responses or wrong/missing
reads**. Native shard totals were **335,300 / 333,400 / 331,300 orders**, with
**3,353 / 3,334 / 3,313 customers**; each total and every complete body matched on
all three copies of its shard. Final `down`/`docker-down` also passed and left
**0 owned containers, volumes, networks or workload build tags**. Journals from both fixtures remain local.

## Bootstrap gotchas

Initial bootstrap attempts exposed three controller/workload issues: action CLI
output is text while lookup responses are JSON; Vitess rejects even a no-op
UPDATE of the vindex column; the MySQL socket directory is mode 0750 under UID
999. The controller distinguishes response kinds, the idempotent customer seed
uses `name=name` (without repairing corrupted names), and direct checks use that
existing non-root UID. Failed logs remain under the fixture's `bootstrap/`; no
fixture reset was used to recover.

## Historical results before the production backfill

The following 2026-11-14 runs used the earlier per-case reset and count/sum replica checks. They are retained as historical measurements; they do not qualify cross-run history preservation or the new full-body replica oracle. These runs completed live against Docker deployments. Each reconciled **1,000,000 acknowledged and observed orders**, **0 missing acknowledged IDs**, **0 duplicate IDs**, **0 committed rows without a witness**, and **5,000,500,000 cents**. All configured replicas passed the example's catch-up checks.

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

## Historical 2026-11-14 measurements

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
- [Vitess 23 native vindex function](https://github.com/vitessio/vitess/blob/v23.0.0/go/vt/vtgate/engine/vindex_func.go): the keyspace ID oracle; no client-side hash reimplementation.
- [Semi-sync policy source](https://github.com/vitessio/vitess/blob/v23.0.0/go/vt/vtctl/reparentutil/policy/durability.go): one eligible replica acknowledgement.
- [Vitess 23 reparenting source](https://github.com/vitessio/vitess/tree/v23.0.0/go/vt/vtctl/reparentutil): replica eligibility, promotion and repair paths.

Compare NDB's shared distributed storage engine with these independent MySQL shards in the NDB example. The systems expose different transaction and recovery mechanisms even though both configurations retain three copies of customer data.

## Future work / TODO

Deferred checks for supported configurations; these are **not executed coverage**.
Keep recorded passes, failures and limitations above. Performance work requires
a separately selected depth; this list does not restart paused experiments.

- [ ] Reshard customer ranges online and expand/shrink tablets; verify each customer's destination and replica bodies without resetting histories.
- [ ] Test peer partitions, topology/quorum loss and stale-primary rejection across each shard.
- [ ] Exercise planned/rolling maintenance, supported upgrades and resource changes under independent readers/writers.
- [ ] Replace a fenced lost volume, inject bounded storage pressure and verify independent backup/restore.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
