# Vitess deployment and primary recovery

One unsharded keyspace, one MySQL primary and two replicas. This example measures
VTGate/VTOrc recovery with independent reads, continuous writes and preserved
acknowledgement histories. [Customer sharding](../customer-sharding/) is separate.

## Quick start

1. `make docker-up` — start three etcd members, VTctld, VTGate and three tablets; wait for MySQL readiness.
2. `make up` — initialize the keyspace/schema, start VTOrc, build the Go workload and seed 10,000 customers.
3. `make test` — append and reconcile 1,000,000 orders through VTGate and all three native copies.
4. `make down` — verify retained histories, then drop this fixture's tables.
5. `make docker-down` — remove the project's containers, volumes, network and locally built workload tag.

Requires Docker Compose v2, Make and Python 3. Allow at least 5 GiB free Docker
space; histories and MySQL logs accumulate. The pinned Vitess image runs as
amd64 under emulation on Apple Silicon. SQL binds `127.0.0.1:15307`, overridable
with `VITESS_PORT`; local SQL/control endpoints are unauthenticated.

## Setup and durability

| Component | Configuration / limit |
|---|---|
| Vitess | `vitess/lite:v23.0.0`, VTGate, VTctld and VTOrc |
| Topology | Keyspace `orders`, unsharded full-range shard `0`, cell `zone1` |
| Tablets | `t100`, `t101`, `t102`: mysqlctld + mysqld + vttablet, each 1 CPU / 1 GiB |
| MySQL | Observed 8.4.6, 64 MiB buffer pool, GTIDs, flush-on-commit/binlog/relay-log sync all enabled |
| Native semi-sync | One eligible replica ACK, `AFTER_SYNC`, `wait_no_replica=ON`, source timeout `1000000000000000000` ms |
| etcd | Three `quay.io/coreos/etcd:v3.5.21` members, each 0.25 CPU / 256 MiB |
| Routers/controllers | VTGate 1 CPU / 1 GiB; VTctld and VTOrc each 0.5 CPU / 512 MiB |
| Workload | Local Go modules, `database/sql`, go-sql-driver/mysql 1.9.3; each process 2 CPU / 1 GiB |

The native keyspace policy is `semi_sync`: three copies do not mean three
synchronous acknowledgements. The controller verifies native policy, source
status/ACK peers and those timeout/wait settings. It captures a primary GTID
barrier and requires both replica threads to stream from the actual primary UUID,
execute the barrier and serve queries. All three physical UUIDs, fixture markers,
read-only/topology roles and durable MySQL settings must match.

`orders-workload/` contains this example's Dockerfile, Go writer, independent
reader and full-body oracle; `control.py` performs native operations and
`report.py` records phase observations. Source and builds are local to this
example. A separate fixture UUID and all three physical MySQL UUIDs bind the
external witness to its database. Setup verifies unsharded VSchema and shard `0`;
there is no client-side hash or claim of distributed shard placement here.
Repeated setup preserves data and rejects legacy tables without a witness.

Every run reserves disjoint IDs and fsyncs attempts, ACKs and errors outside the
database containers under `results/<fixture-id>/runs/<run-id>/`. Tests never
truncate earlier orders. Retries reuse deterministic unique keys; a complete
ambiguous commit is reported separately. Every historical order ID, customer,
amount and all 10,000 customer names are checked through VTGate and directly on
**all three MySQL copies**. Missing ACKs, wrong bodies, duplicates, unattempted
rows and partial transactions fail. Direct verifiers use the image's existing
non-root MySQL UID 999 and a read-only mount of that exact owned volume to reach
its Unix socket. VTGate scans pin one connection in OLAP mode, keeping OLTP caps.

The independent reader samples immutable ACK bodies from 16 different customers,
or seeded customer 0 before the first history exists. It uses PRIMARY routing,
a 3 s deadline and 100 ms delay between probes. It runs before injection, through
native repair and final verification; blocked writers do not stop read observation.
Fresh-write visibility lag and continuous replica stale-read behavior are unmeasured.

## Operational commands

| Command | Action |
|---|---|
| `make test-production` | Serial million-order baseline, primary crash, replica pause, replica crash, VTGate crash and final historical verification |
| `make failover` | SIGKILL actual primary, prove exit 137, require a different primary and read/write progress while the old primary stays down, then repair it |
| `make replica-pause` | Pause an actual replica for 10 s, resume and verify native GTID/thread/query-service recovery |
| `make replica-crash` | SIGKILL one non-primary tablet, require degraded progress, restore its retained volume and verify all three copies |
| `make router-failure` | Kill VTGate for 10 s, observe request failures and prove native/application recovery |
| `make verify` | Reconcile every historical witness through VTGate and on all three copies |
| `make benchmark` | Append a workload without a dispatch-rate ceiling, retaining earlier data |
| `make test COUNT=2000000` | Append two million orders |
| `make unit-test` | Docker format/vet/test/build checks plus controller/report negative tests |
| `make status` / `make cli` | Native tablet topology / SQL shell |

Defaults: `COUNT=1000000 CUSTOMERS=10000 WORKERS=8 BATCH=100`.
`CHAOS_RATE=25000` caps live fault dispatch, not measured capacity. Live runs
reserve `MAX_ROWS=10*COUNT` IDs and stop only after native repair, controller stop
request and the minimum count. Cases prove affected-keyspace progress before
injection and after repair; primary/replica crashes also require progress while
the target remains down. The controller verifies eight unrelated containers and
the unaffected mysqld PID/start times retain their identities. Failed cases retain
their journals and restore only the injected target; do not reset failed fixtures.

## Limits

One shard and three tablets remain fixed. Resharding, tablet expansion/shrink,
planned/rolling maintenance, upgrades, vertical changes, peer partitions,
stale-primary rejection, topology/quorum loss, permanent disk loss/write-I/O
failure, bounded pressure, independent backup/restore and controlled single-row,
cold/warm query comparisons are **unrun**. A process crash with retained storage
is not disk loss. All copies, etcd members and the external journal share one
Docker VM and physical host; these tests do not establish host/power-loss
survival or production security. Total ACK-peer loss is a separate unrun case.

## Measured results

2026-11-16, Apple M4 Pro, Docker 29.5.3 aarch64 VM, **11 vCPUs / 24.4 GiB**;
Vitess/MySQL amd64 emulated, Go workload native. Effective limits are recorded in
`resources.json`: 9 long-running infrastructure services capped at an aggregate
5.75 CPU / 5.75 GiB; two concurrent workload processes add 4 CPU / 2 GiB.
These are shared CPU quotas, not dedicated cores. One run per case, 10,000
customers, eight writer connections, 100-row customer-local transactions. The
live fault workload is rate limited to at most 25,000 rows/s; datasets grow between
cases. These timings are not a capacity estimate or a fair product comparison.

Fixture `18ed4662-94eb-4fbf-b959-1676b7411034` reconciled
**6,184,600 ACK orders across 6 histories**, plus all 10,000 customers.
Every completed case checked VTGate and all three native copies: **0 missing ACKs,
wrong values, duplicate IDs, unattempted rows, partial transactions
or committed-without-ACK rows**. Independent observers recorded **0 wrong/missing
reads**. Failed requests remain visible and are counted separately from successful
requests. Raw evidence stays in ignored `results/`, including phase-specific
success/error latency and throughput, per-keyspace progress and survivor identities.

| Case | New ACK orders | Writer elapsed s | Orders/s | Failed attempts / ambiguous commits | Promotion s | Native repair barrier s | Longest read-success gap s |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 1,000,000 | 36.415 | 27,461 | 0 / 0 | — | — | 0.110 |
| Primary crash | 1,052,100 | 98.512 | 10,680 | 216 / 4 | 3.551 | 69.302 | 2.885 |
| Replica pause | 1,000,000 | 41.690 | 23,986 | 0 / 0 | — | 13.104 | 0.147 |
| Replica crash | 1,132,500 | 47.301 | 23,942 | 0 / 0 | — | 21.411 | 0.191 |
| VTGate crash | 1,000,000 | 52.966 | 18,880 | 816 / 4 | — | 4.648 | 11.321 |
| Post-recovery benchmark | 1,000,000 | 60.363 | 16,567 | 0 / 0 | — | — | 0.229 |

Promotion includes the SIGKILL RPC and 1 s topology polling. Repair timing begins
after Compose has restored the victim's MySQL health and includes the complete
three-copy GTID/role/query-service barrier under continuing load. Crashes hold the
victim down for 10 s after the required degraded progress (VTGate uses a fixed
10 s outage); pauses hold for 10 s. Read gaps include the single-keyspace probe
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
| Baseline | 26.049 / 52.532 (10,000) | — | 1.016 / 3.006 (450) | — |
| Primary crash | 29.601 / 861.784 (10,521) | 2.069 / 26.371 (216) | 1.159 / 2.457 (1,300) | 0.464 / 1.744 (27) |
| Replica pause | 7.898 / 52.655 (10,000) | — | 0.951 / 5.962 (570) | — |
| Replica crash | 6.798 / 51.271 (11,325) | — | 0.959 / 3.409 (698) | — |
| VTGate crash | 6.193 / 54.162 (10,000) | 4.571 / 12.974 (816) | 0.966 / 13.977 (588) | 8.986 / 15.838 (101) |
| Post-recovery benchmark | 27.197 / 191.590 (10,000) | — | 1.147 / 3.976 (779) | — |

| Case | Local run ID |
|---|---|
| Baseline | `20261116T083418-759cac4d` |
| Primary crash | `20261116T083514-8c8541f6` |
| Replica pause | `20261116T083755-0d7571ef` |
| Replica crash | `20261116T083909-d0664c08` |
| VTGate crash | `20261116T084039-782c43a3` |
| Post-recovery benchmark | `20261116T084229-b115bd80` |

## Fresh-volume lifecycle

After the six-history suite, repeated `make up`, `make status`, `make unit-test`,
`make down` and `make docker-down` passed, with zero owned containers, volumes,
networks or workload tags left behind. A complete five-command lifecycle then
created fixture `344e3016-8268-40e6-aee9-a7cce3ece70b` with all three native
UUIDs recorded at initial bootstrap. Run `20261116T084652-3ebb3f44` reconciled
**1,000,000 ACK orders and 10,000 customers on all three copies**, in **29.583 s /
33,803 orders/s**, successful-attempt p50/p99 **20.189 / 44.295 ms**. Retries,
ambiguous responses, wrong/missing reads and every reconciliation error were
**0**. Final `down`/`docker-down` passed and left **0 owned containers, volumes,
networks or workload tags**. Journals from both fixtures remain local.

## Bootstrap controller finding

The first adaptation accidentally registered only `t100` in the external physical
identity witness. The added strict guard rejected `test-production` before any
order allocation or write. That attempt is not a qualified setup or recovery run.
Before completing the witness, the repair verified project/container ownership,
the original primary UUID, identical fixture markers, all 10,000 customer names,
zero orders/histories, both native replica threads/source UUIDs and the captured
GTID barrier. The original incomplete witness, failed test log and proof are
retained under `bootstrap/`. The corrected controller registers all three copies
at creation and rejects an incomplete witness; regression tests cover both guards.
No database reset or row repair was used. The subsequent fresh-volume lifecycle
below qualified the corrected bootstrap independently.

## Historical 2026-11-14 results

These earlier runs reset orders per case and used count/sum replica checks. They do not qualify cross-run history preservation or the new full-body copy checks. The following runs completed live against Docker deployments. Each reconciled **1,000,000 acknowledged and observed orders**, **0 missing acknowledged IDs**, **0 duplicate IDs**, **0 committed rows without a witness**, and **5,000,500,000 cents**. All configured replicas passed the example's catch-up checks.

| Executed scenario | Local run ID |
|---|---|
| Baseline | `20261114T181042Z` |
| Primary failure | `20261114T181521Z` |

The two-million-order and row-at-a-time comparison variants were not run.

Audit the raw evidence in `results/<run-id>/`: `load.json`, `check.json`, `acknowledged.jsonl` and, for fault/scaling runs, `failover.json`. Journals and raw reports remain local and are ignored by Git. Timing, phase latency, offered-rate limits and shared-VM conditions are detailed below; these runs do not provide a controlled cross-system ranking.

Batch latency includes transaction attempts and retries; it excludes the subsequent journal fsync, which is included in overall elapsed time and throughput.

### Historical measurements

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
