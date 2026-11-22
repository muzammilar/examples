# CedarDB backup and process recovery versus PostgreSQL

## Quick start

1. `make docker-up` — build the local Go workload and start the two isolated database services.
2. `make up` — seed one million deterministic orders per backend, then verify every body.
3. `make test` — run controller checks and full retained-data reconciliation.
4. `make down` — stop the example, retaining its volumes and witness histories.
5. `make docker-down` — remove owned infrastructure/state, preserving ignored result journals.

Docker Compose, a local Unix Docker API and host Python 3.11+ are required.
No host Go compiler is needed. No host ports are exposed. The example contains
its own Go source, Dockerfile, module pins, deployment, oracle and controllers.
Both backends have 4 CPUs / 6 GiB from startup. Each independent writer/reader
has 1 CPU / 512 MiB. They share a Docker VM; this is not independent-host HA.

The five commands were also run separately against a fresh fixture on
2026-11-21: **2,000,000 seeded rows** passed complete body verification and native
duplicate/missing-key controls. Setup took **61.30 s**, tests **95.32 s**, and
both cleanup steps exited successfully. No example containers or volumes remained;
unrelated resources were retained. See [LIFECYCLE_RESULTS.json](LIFECYCLE_RESULTS.json).
Use `make cli` for an interactive CedarDB SQL session.

[Analytical contention under concurrent appends](HTAP.md) extends this same
infrastructure with `make benchmark-htap`: a fixed million-row aggregate, alone
and alongside independent live writers/readers. Its qualification/results are
tracked separately from the recovery cohort below. The completed 12-cell cohort
retained all **109,580 historical ACKs**. For this fixed aggregate, median query
throughput was **3.33×** PostgreSQL alone and **2.69×** under live traffic;
PostgreSQL used less charged memory. See the trial spread, resource costs and
UTC/monotonic timing audit in that report.

[Transactional updates and delete/reinsert](MUTABLE.md) now add a separate Go
workload over data that analytical queries actually read. Four **10,000-row**
healthy/SIGKILL cases pass with zero ACK loss or wrong/partial rows. A four-cell
short benchmark-harness diagnostic passes; the repeated **million-row** mutation
comparison is still pending. Its numbers must not be substituted for the
append-only results above.

Run operational cases before cleanup, one command at a time:

```sh
make graceful
make crash
make backup
# Or run all three serially against both backends:
make test-production
```

`ROWS=1000000` and `RATE=50` are overridable. These are bounded live operational
observations, not saturation or sustained-capacity measurements. Read/write
actors run independently through the outage or native online backup. A stopped
single instance is expected to be unavailable. The final verifier checks every
old/new body against a Python oracle and the durable Go witness, not just counts.
New histories never reuse IDs. Verified CSV exports are removed only after proof
metadata is fsynced; failed exports and histories remain evidence.

## Durability and backup boundaries

CedarDB Community Edition **v2026-11-18** supports `BACKUP ALL TO` without an
Enterprise key: the capability probe created a native backup containing its
database and WAL files and one known row. The million-row Go COPY probe also
completed. The [measured cohort below](#measured-operational-results) now covers
six live graceful/crash/backup cases, restored read-only transactions, full body
readback, and native duplicate/missing-key controls. Seven host controller tests
and the Go descendant-cancellation test pass.

The driver forces and verifies `async_commit=off` on every CedarDB connection.
The default may be asynchronous on consumer disks. PostgreSQL uses synchronous
commits, fsync and full-page writes. Neither Docker process-crash testing nor
one-host copies establishes physical power-loss durability.

Backups run under traffic, then are copied into new, example-owned restore
volumes. The original backup is mounted read-only during cloning. All seed rows
and pre-backup ACKs must survive; later ACKs are counted separately as snapshot
RPO. A fresh process is not a fresh dataset: repeat `make up` to verify retained
state, without reloading it. Keep failed native storage until it is reconciled.

The resource recorder saves native CPU/memory/block-I/O samples at about 1 s,
including unavailable samples. CPU reset intervals remain explicit gaps. Native
cgroup peak/counter snapshots, database disk usage, driver process CPU/RSS,
full-output bytes and off-path CSV gzip costs supplement the series. Export gzip
measures corpus compressibility, not native engine compression.

## Operational gotchas

- The database/backup volumes are separate Docker volumes on **one physical host**.
  These copies do not qualify host loss, independent-disk failure or site recovery.
- CedarDB CE allows table ownership but gates `GRANT` behind Enterprise (observed
  SQLSTATE `0A000`). Its actors use a non-superuser `orders_owner`; reader and writer
  cannot have separately granted table privileges in this free edition. PostgreSQL
  uses separate limited reader/writer roles and a replication-only `backup_agent`.
- Restored connections use explicit `BEGIN READ ONLY`; negative writes must fail
  with SQLSTATE `25006` or `42501`, followed by complete unchanged-body verification.
- `pg_basebackup` needs an authenticated **replication** HBA rule, not only a normal
  database login. The checked-in init script and repeatable configuration path
  install/reload that rule and inspect `pg_hba_file_rules`. The original rejected
  attempt is preserved as a failed case; its source data was fully reconciled.
- Setup/clone/backup utilities have 1 CPU / 512 MiB caps and report native
  CPU/RSS/cgroup counters. Helper cancellation kills its own process group so
  descendants cannot keep a timed-out copy alive. No unrelated process is signaled.
- Failed fixtures are not reseeded. Fix and reconcile retained state before
  deleting storage. A successful `docker-down` marks an intentionally destroyed
  fixture; a subsequent lifecycle gets a new generation while older journals remain.
- `make report` validates recorded source hashes, budgets and a common workload
  image. Run it after `make test-production` and before infrastructure cleanup.

## Research and limits

- [CedarDB release notes](https://cedardb.com/docs/releases/) introduce native backups in the pinned November release.
- [Backup API](https://staging.cedardb.com/docs/references/utility/backup/) specifies online backup and same-version restore; the current stable documentation URL was unavailable during research, so the API is also checked against the actual pinned image.
- [Write caching](https://cedardb.com/docs/references/writecache/) documents hardware-dependent defaults and `async_commit=off`.
- [Configuration](https://cedardb.com/docs/references/configuration/) describes startup worker and memory budgets.
- [Feature roadmap](https://cedardb.com/docs/roadmap/) still lists read replication/automatic failover as planned; no invented CedarDB cluster is deployed.
- [CedarDB SQL compatibility](https://cedardb.com/docs/compatibility/sql_features/) identifies Enterprise privileges and supported read-only transactions.
- [PostgreSQL base backups](https://www.postgresql.org/docs/17/app-pgbasebackup.html) and [HBA rules](https://www.postgresql.org/docs/17/auth-pg-hba-conf.html) describe the replication connection and reload/inspection behavior.
- [Pinned pgx source](https://github.com/jackc/pgx/blob/v5.7.6/pgconn/pgconn.go) implements native COPY/cancellation. CedarDB's engine is closed source; its shipped entrypoint was inspected and executes the native process as PID 1.

This initial scope does not qualify disk replacement, network partitions,
resource exhaustion, upgrades, backup interruption, arbitrary SQL isolation
anomalies, cold-cache queries or performance winners. Native CedarDB physical
backups and PostgreSQL streamed base backups have different implementation and
recovery boundaries; compare measured outcomes, not backup file counts.


<!-- verified-results -->

## Measured operational results

**2026-11-21**, generation `0302eb85e7db4c379f476523b444c8b4`: **1,000,000 seeded orders per backend**, 10,000 customers, 256-byte synthetic payloads. Six serial cases at equal **4 CPU / 6 GiB** database caps; independent **1 CPU / 512 MiB** actors, maximum 50 requests/s each. These are short operational observations, not capacity benchmarks or repeated-trial latency rankings.

The input stream contains **274,666,790 CSV bytes per backend**, reconstructed from the deterministic encoder (without header/protocol framing). Each row has 256 payload bytes; persisted size and allocated space are measured separately.

The fixture also preserves **6 earlier successful histories** from qualification refinements. The table selects the newest case per backend/action; runtime source hashes, workload image and resource budgets match across those six cells.

All six cases passed complete seeded/live body reconciliation and offline latest-read lower-bound checks. Graceful stops exited 0; SIGKILL targets exited 137 and restarted with retained volumes. The unrelated backend did not restart. Counts below include connection and shutdown errors; ACK totals are cumulative within each backend's fixture. Ambiguous writes remain separate from ACKs, including when their bodies appear after recovery.

| Backend | Case | Historical live ACKs | Write fail | Write ambiguous | Read fail | Longest write/read success gap s |
|---|---|---|---|---|---|---|
| cedar | backup | 8598 | 0 | 0 | 0 | 0.065 / 0.042 |
| cedar | crash | 5674 | 181 | 1 | 182 | 5.646 / 5.710 |
| cedar | graceful | 5005 | 184 | 1 | 186 | 5.707 / 5.799 |
| postgres | backup | 8507 | 0 | 0 | 0 | 0.145 / 0.031 |
| postgres | crash | 5569 | 276 | 1 | 178 | 5.564 / 5.566 |
| postgres | graceful | 4895 | 176 | 1 | 177 | 5.545 / 5.546 |

The restart cases deliberately hold the only instance down for **5 seconds**. Success gaps include that hold, pacing and reconnection; they are not leader-election time or independent-host failover. Successful and failed request latency remain separate in the JSON.

| Backend | Case | Actor | Baseline n; p50/p99 ms | Recovered n; p50/p99 ms |
|---|---|---|---|---|
| cedar | backup | writer | 252; 4.692 / 7.426 | 264; 4.784 / 8.931 |
| cedar | backup | reader | 252; 5.326 / 6.542 | 265; 5.353 / 6.564 |
| cedar | crash | writer | 265; 4.197 / 7.985 | 250; 4.111 / 7.966 |
| cedar | crash | reader | 265; 5.483 / 10.009 | 250; 5.548 / 6.383 |
| cedar | graceful | writer | 264; 4.147 / 11.726 | 264; 4.160 / 4.753 |
| cedar | graceful | reader | 263; 5.388 / 11.230 | 264; 5.339 / 9.033 |
| postgres | backup | writer | 259; 0.676 / 5.960 | 257; 0.589 / 3.106 |
| postgres | backup | reader | 259; 0.434 / 2.310 | 257; 0.438 / 1.681 |
| postgres | crash | writer | 256; 0.512 / 2.387 | 260; 0.644 / 2.161 |
| postgres | crash | reader | 256; 0.446 / 1.859 | 259; 0.655 / 2.407 |
| postgres | graceful | writer | 257; 0.535 / 2.827 | 262; 0.824 / 2.371 |
| postgres | graceful | reader | 257; 0.401 / 1.924 | 262; 0.599 / 2.143 |

Request clocks bracket connection/configuration when needed, SQL and body validation. Writer intent fsync occurs before the request timer; final ACK journal sync is outside it. Phase rates include pacing/witness costs. Paired reads fetch one rotating seeded key and the newest key; these fit in memory and do not qualify cold storage.

| Backend | Case | Observed CPU core-s | Mean CPU (1 core=100%) | Charged memory mean/max MiB | Native peak since last start MiB | DB allocated MiB |
|---|---|---|---|---|---|---|
| cedar | backup | 51.366 | 61.4% | 788.0 / 799.3 | 805.8 | 1604.8 |
| cedar | crash | 13.428 | 31.6% | 633.8 / 712.4 | 719.2 | 1604.8 |
| cedar | graceful | 15.678 | 37.0% | 632.5 / 900.2 | 710.2 | 1603.9 |
| postgres | backup | 3.889 | 4.6% | 401.3 / 402.8 | 886.9 | 651.3 |
| postgres | crash | 2.078 | 4.9% | 244.6 / 400.6 | 401.4 | 666.4 |
| postgres | graceful | 2.011 | 4.7% | 242.6 / 402.4 | 401.3 | 666.1 |

| Backend | Case | Observed read MiB / MiB/s | Observed write MiB / MiB/s |
|---|---|---|---|
| cedar | backup | 24.746 / 0.296 | 108.199 / 1.294 |
| cedar | crash | 35.879 / 0.972 | 7.180 / 0.194 |
| cedar | graceful | 35.840 / 0.944 | 11.059 / 0.291 |
| postgres | backup | 0.000 / 0.000 | 0.789 / 0.009 |
| postgres | crash | 0.000 / 0.000 | 0.184 / 0.005 |
| postgres | graceful | 0.000 / 0.000 | 0.000 / 0.000 |

Per-device kernel block counters, not logical query bytes or proof of physical host traffic. Docker Desktop sharing/cache can move I/O outside a container. Native io.stat operation counters remain in snapshots; Docker API IOPS/latency/queue fields are unavailable on this cgroup-v2 runtime rather than zero.

CPU values cover observed counter intervals, **excluding reset/initial/final partial intervals**, not complete CPU totals across crashes. Native memory peaks are lifetime-since-start counters, not per-phase peaks; sampled means/maxima cover the case. Database bytes include catalog/index/WAL data. Shared backups are counted once in the disk series; restore copies, actor peaks, native client CPU/RSS, controller costs, per-device I/O bytes/rates and phase summaries are retained in [VERIFIED_RESULTS.json](VERIFIED_RESULTS.json). Raw samples/history remain ignored under `results/`.

**cedar restore:** 1,000,000 baseline rows and **5,951 pre-backup ACKs** verified, **0 required missing**. **2,609 later source ACKs** were outside the restored snapshot at case end: measured snapshot RPO, not source data loss. Writes to the restore session were rejected and its complete body remained valid. The archive is mounted read-only for cloning; the database opens a separate verified copy on fresh owned storage.

**postgres restore:** 1,000,000 baseline rows and **5,854 pre-backup ACKs** verified, **0 required missing**. **2,591 later source ACKs** were outside the restored snapshot at case end: measured snapshot RPO, not source data loss. Writes to the restore session were rejected and its complete body remained valid. The archive is mounted read-only for cloning; the database opens a separate verified copy on fresh owned storage.

**Native CedarDB visible-column cold statistics after load:** 287,925,840 uncompressed / 272,416,544 encoded / 50,933,760 compressed bytes, combined **5.653×** at that layer; codecs/encodings and per-column values are in the JSON. Internal transaction columns are excluded. These are native reported bytes, not the CSV or complete database directory.

**PostgreSQL payload storage after load:** 1,000,000 cells, 260,000,000 pg_column_size bytes, codec NULL (not compressed). This includes value headers and is a different layer from CedarDB's cold-column statistics.

A post-case file audit explains the larger CedarDB whole-store footprint: `db.ballast` occupied about **1 GiB**, two `db.wal.*` files about **256 MiB each**, and `db.0_0` about **49 MiB**. PostgreSQL's `base` directory was about **347 MiB** and `pg_wal` about **288 MiB**. These are allocated-file observations at audit time, not compression ratios or claims about the purpose/reclamation of undocumented files. Raw paths/bytes are retained in the JSON.

One final verified source export in this cohort contained **277,057,657 bytes**; its streamed gzip level 1 output was **61,241,492 bytes**, ratio **4.524×**. This is CSV compressibility, not engine compression. Native CedarDB cold-column codec/byte statistics and PostgreSQL payload compression/storage statistics are separately recorded; their layers and coverage differ. Gzip CPU/wall costs are timed without file/hash work; native automatic-compression CPU remains mixed into whole-container costs.

Development evidence remains separate: the original remote-replication authentication failure stays failed; all source ACKs were independently reconciled afterward. Enterprise-only GRANT and the unrelated host-variable routing probe remain documented limitations/failures. Setup's null-IOPS postprocessor error was repaired from preserved raw samples without reloading data; its original failure is retained. Earlier development CLI CPU percentages used incompatible timing boundaries and are excluded from this qualification; final clients use matched main-window CPU/wall deltas.

Remaining: disk loss/EIO/full-disk, partitions, pressure, upgrade/rollback, interrupted backup, schema/transaction anomaly workloads, sustained repeated benchmarks and genuine cold-cache behavior. A correctness/recovery pass does not establish complete production readiness.

<!-- /verified-results -->
