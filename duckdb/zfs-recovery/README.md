# DuckDB on replicated ZFS: clients, snapshots and failover

Multiple DuckDB client processes write through one Quack server. Its native
DuckDB database and WAL live together on ZFS. A second Linux guest receives
snapshots, exposes read-only clones and becomes the owner after a fenced-primary
power cut. This tests asynchronous disaster recovery, not multi-master writes.

The expanded five-command lifecycle passed on fresh guests on 2026-11-22.
Full-body, resource and teardown evidence is in [FULL_SUITE_RESULTS.json](FULL_SUITE_RESULTS.json).
The client timeout limitation below remains unresolved; this is functional
recovery evidence, with no production-readiness or performance ranking claim.
DuckLake is a separate backlog item. For filesystem-only mirror, RAIDZ2 and
replication experiments, see the [dedicated ZFS lab](../../zfs/storage-lab/README.md).

## Quick start

Prerequisites: ARM64 Docker with Compose v2, Python 3.11+, Make and `ssh-keygen`. Allow 8 GiB
Docker RAM and 12 GiB free Docker disk. Virtual disks are sparse; do not fill the host filesystem. This example boots two actual Linux
kernels with QEMU software emulation inside containers without `--privileged`. The first
image download and guest provisioning take several minutes. No host ZFS module,
privileged container, host device or Docker socket is required.

1. `make docker-up` — build the VM/client image; boot two Linux guests and wait for ZFS readiness.
2. `make up` — install pinned DuckDB artifacts, create the guests' dedicated pools and start the primary owner.
3. `make test` — run disk replacement, continuous retained-disk VM crash/recovery, then asynchronous standby recovery.
4. `make down` — stop the reachable database owner; keep the guest infrastructure and disks.
5. `make docker-down` — delete this project's guests, virtual disks, network, image tag and generated SSH key.

Test state is isolated to example-owned virtual disks. Independent legacy cases
rename their previous datasets instead of erasing earlier native state. Existing
witness filenames are never silently overwritten; use a new `RESULTS_DIR` for
another independent recorded lifecycle. Promotion powers off the old primary and leaves the standby serving writes.
Final archive verification boots the old guest with its database service still
fenced; only fresh clones are recovered, and standby remains the sole writable
owner. To repeat, use the complete clean lifecycle.
`up` refuses to restart a stale primary after promotion; no service is enabled to
start automatically when a fenced VM reboots. `make status` shows both roles.
`make cli` opens an SSH shell in the primary (before its deliberate power cut).

| Make variable | Default |
|---|---:|
| `DISK_GIB` | 16 GiB per data device; two-device mirror plus replacement per guest |
| `QUERY_RUNS` | 20 measured queries after 2 warmups |
| `BENCH_ROWS` | 200,000 additional rows for `make benchmark` after `make test` |
| `ROWS` | 2,000,000 |
| `CUSTOMERS` | 10,000 |
| `CLIENTS` | 4 independent client processes |
| `BATCH_ROWS` | 10,000 |
| `BATCH_DELAY_MS` | 1000 |
| `RESULTS_DIR` | `results/manual` |

`CLIENTS` is limited to 1–8 for this lab. For the fault phase, `ROWS` must be at least `20 * BATCH_ROWS`. Keep enough work
in flight to observe replication and failure; a tiny smoke test cannot establish
that behavior. Successful batches are journaled and fsynced outside both guests.
Failed requests have ambiguous outcomes and are recorded without blind retries.

## Topology and ownership

```text
4 independent DuckDB clients ─Quack─> Primary DuckDB owner
                                         │ native database + WAL
                                   Primary ZFS pool
                                         │ snapshots / incremental send
                                   Standby ZFS pool
                                         ├─ recovered read-only clone → concurrent readers
                                         └─ fence primary → recover → start new Quack owner
```

ZFS replicates snapshot state; it does not supply database transaction locks
between independent owners. Every writable client goes through the one active
Quack server. Read-only clones are separate filesystems: first recover/checkpoint
a writable clone, then make it read-only before attaching independent readers.
Clients never open a receiving filesystem while an incremental receive mutates
it. Database and WAL share one dataset so snapshots capture them together.

Each guest has a two-device mirror of sparse 16 GiB virtual disks (about 16 GiB
usable before ZFS overhead), a third 16 GiB replacement disk and a sparse 12 GiB
OS disk. Set `DISK_GIB=32` for the complete lifecycle to use 32 GiB devices; valid
values are 8–64 GiB. Changing size requires a clean lifecycle, not implicit
resizing of retained state. Stable virtio serial IDs identify only example-owned
virtual disks. No physical disk is passed through.

Datasets use `compression=lz4`, `atime=off`, `recordsize=256K`, default
`sync=standard` and a 256 MiB ARC cap. Database/WAL sync behavior is not disabled.
DuckDB's `256MB` checkpoint threshold keeps an active WAL for the crash test.
The record size is a lab choice matching DuckDB blocks, not a universal optimum.

## Directory structure

```text
zfs-recovery/
├── Makefile                 # lifecycle; separate disk, crash, replica and benchmark targets
├── docker-compose.yml       # two unprivileged QEMU containers and client controller
├── app/
│   ├── server.py            # single DuckDB/Quack writable owner
│   ├── lab.py               # disk replacement and asynchronous replication controller
│   ├── live.py              # independent continuous writers/readers through guest recovery
│   ├── history.py           # fsynced intents/outcomes and independent full-body oracles
│   ├── archive_guest.py     # native recovery/export of fresh verification clones
│   ├── archive_verify.py    # all-generation and primary-tail reconciliation
│   ├── export_csv.py        # native COPY with the external witness CSV dialect
│   ├── export_control.py    # legacy/live byte parity and overwrite controls
│   ├── fencing_control.py   # actual SSH timeout and exact-process fencing control
│   └── test_history.py      # oracle and malformed-evidence negative controls
├── production.py            # host Compose fencing, resource collection and publication
├── test_production.py       # counter/reset and teardown registration controls
├── live-contract.json       # declared semantics, deadlines and recovery expectations
├── LIVE_RECOVERY_RESULTS.json # curated latest continuous recovery evidence
├── ARC_CHECKPOINT_RESULTS.json # separately preserved ARC-qualified checkpoint
├── vm/
│   ├── Dockerfile           # pinned Linux image, offline ZFS and DuckDB runtime
│   └── boot.py              # guest boot and owned sparse virtual disks
├── state/                   # ignored generated SSH keys; disks reside in named volumes
└── results/                 # ignored JSON reports and external per-client ACK journals
```

## Separate correctness tests

| Target | Fault and required result |
|---|---|
| `make disk-test` | Mark one mirror leg FAULTED with `zpool offline -f` during active writes. Prove more batches acknowledge while DEGRADED, replace with the third disk, wait for resilver and scrub, and reconcile every acknowledged batch. |
| `make crash-test` | The new continuous profile below keeps independent writers/readers active through the whole retained-disk guest crash and restart; verifies exact seed and mutation bodies. |
| `make replica-test` | Snapshot/send/resume tests below, then fence the old owner and promote the asynchronous standby. Require zero loss from replicated phases; report the intentionally unreplicated tail separately. |
| `make test` | Run those three tests serially; the replica test leaves the standby active. |
| `make benchmark` | After successful `make test`, append `BENCH_ROWS` with no deliberate delay and measure warm full/customer queries. |

`disk-test` consumes its replacement disk; repeating the complete suite needs a
fresh lifecycle. Mirror faulting is controlled ZFS fault injection, not physical
hot-unplug or silent-corruption injection. The VM-crash recovery timer includes
VM restart and health waits; replica-promotion timing does not restart a guest.

## Continuous retained-disk recovery

`make test-production` aliases the complete `make test`: controls, disk replacement,
continuous retained-disk crash, replica promotion and final all-history export.
`make crash-test` runs the continuous profile independently. Its checked-in [contract](live-contract.json) defines
**2,000,000 seed rows**, **10,000 customers**, four independent writers and one
independent reader. Each batch has 1,000 unique IDs; every row includes its
customer, amount, 64-character SHA-256 payload and complete batch bounds. A case
owns a fresh UUID table; it never resets or drops previous case tables to recover.

This extends the existing Python/native DuckDB and SSH/QEMU harness, preserving
its deployment and protocol instead of rewriting it just to change languages.
All common tasks use the standard library; the official pinned DuckDB binding
executes the SQL. Applications run inside the example's Compose
`live-controller` service, without a Docker socket or host DuckDB installation.

The host controller fsyncs a new case config under ignored
`results/<run>/live/<uuid>/`. Writers fsync an intent before each mutation and a
separate outcome afterward. They never replay failed/ambiguous mutations; the
next request uses a disjoint range. Readers independently check the first/last
seed bodies and the first/last rows of an acknowledged recent batch throughout
the operation. An
offline oracle recomputes every exported body and read result from the ID/config,
checks batch atomicity and reconciles applied unacknowledged intents separately.
Equal counts alone cannot pass. This does not establish general linearizability
or serializable SQL isolation.

Both applications remain alive before injection, while the exact primary QEMU
process is stopped, through guest boot/native WAL recovery, and for at least
10 seconds after successful progress returns. The host proves the exact owned
container is stopped with PID 0, holds it down for at least 5 seconds, then uses
`docker start` on that same container ID and retained disks. The recovered guest
must have a different boot ID and a healthy native mirrored pool. The provisioned
standby is stopped before this profile: it does not replicate the fresh case
table and provides no HA guarantee. Its unchanged stopped identity is checked.

The active budget is **2 CPUs / 3 GiB** for the primary QEMU container and
**2 CPUs / 1 GiB** for the controller including its five application processes;
swap is disabled. The guest has 2 virtual CPUs / 1,536 MiB, DuckDB `512MB` and a
256-MiB ZFS ARC cap. Independently capped functional labs may run concurrently;
co-tenancy is recorded. Timings are diagnostic TCG-emulation observations, not
native capacity comparisons.

Live requests configure `http_retries=0` and `http_timeout=3`. Seed and
complete export use a separate **300-second** administrative timeout. The host bounds guest health at 900 seconds and the continuous controller at
1,800 seconds; each live-progress gate has a 120-second deadline. Those are
lab timeouts, not production recovery SLAs. Operation
latency includes client creation, native request/response, reconnection and error
cleanup, and excludes external journal fsync; write intent-to-response cost is also retained. Completion rates
include pacing and witness overhead. Successful and failed/ambiguous latencies
are reported separately by phase. The fresh run observed a **75.058-second**
ambiguous writer attempt despite the configured timeout; fault-phase failed-write
p99 was **53.987 seconds**. The traces do not split connection, query and cleanup
time, so the responsible component is unresolved. This setting has **not**
established a three-second end-to-end bound. No such availability SLA is claimed. Recent and historical reads share one query;
this is not a cold-cache benchmark. Exact installed guest/controller engine,
Quack and server-source hashes are checked before mutations; distinct built
image IDs remain recorded when only controller source changed during development.

Native cgroup CPU/user/system and charged-memory/high-water counters, per-device
read/write bytes and operations, network counters and sparse disk-file allocation
are sampled through seed, fault, recovery and verification. Native ZFS logical,
physical-accounting and compression properties are separate from off-path CSV
gzip1 compressibility. Physical device latency/queue depth is unavailable in this
Docker/QEMU lab. Raw samples and actual cadence remain in the ignored case tree.

`make verify-archives` reopens the fenced primary, checks its writable service is
inactive with PID 0, and recovers **fresh clones** of every retained dataset.
The originals are never opened writable. It exports each clone read-only and
recomputes every UUID and legacy row body from external witnesses, including the
unreplicated acknowledged primary tail. It inventories native dataset GUIDs and
tables; an unknown nonempty generation blocks cleanup. The promoted standby is
verified independently while it still serves, before `make down` stops it.
These same-pool clones are verification copies, not backups.

`docker-down` first replays every new live case's external CSV and witnesses,
plus the final all-history exports. Legacy targets register their results base
against both owned volumes' creation identity **before** writing; changing
`RESULTS_DIR` at teardown cannot bypass their verification. `make benchmark`
refreshes the archive after adding new rows. Witness hashes reject stale exports.
Incomplete/failed cases block native volume deletion until their state is
independently preserved; errors are retained instead of turning a new clean
fixture into a successful recovery. Earlier disk and asynchronous-replication
results below retain their historical scope; their continuous observation and
full production matrix remain separate follow-up work.

## Fresh complete lifecycle: 2026-11-22

All five commands passed separately with default **2,000,000 rows**, **10,000
customers** and the original **6 CPU / 7 GiB** maximum topology. Each VM has
2 CPUs / 3 GiB; the controller has 2 CPUs / 1 GiB. During the continuous crash
profile the unused standby is stopped, giving an active 4 CPU / 4 GiB budget.
The implemented four-stage matrix took **1,533.831 s (25.56 min)**, excluding
initial provisioning, preflight controls and final teardown. This is QEMU TCG on
an Apple M4 Pro with an 11-CPU Docker VM, not native-machine capacity.

The fresh run passed 19 host controls, native CSV byte/overwrite controls for both
schemas, and a real SSH-timeout/pidfd-fencing control with the database owner
unchanged. A post-promotion `make up` failed for the expected stale-primary guard.
Both owners were then independently verified inactive/PID 0 with no exporter
left running. Healthy Docker listings proved containers, volumes, network and
image tag absent; generated keys were removed. All nine unrelated stopped
`inbox-*` containers retained their original identities/state.

| Recovery and exact-body check | Observed |
|---|---:|
| Disk replacement: final rows / missing ACKs / wrong or duplicate bodies | 2,000,000 / 0 / 0 |
| Acknowledged after the mirror fault while degraded | at least 20,000 rows |
| Replacement wait / scrub and checks | 11.532 / 8.519 s |
| Continuous crash: seed / acknowledged mutation rows | 2,000,000 / 323,000 |
| Crash: complete bodies / missing ACKs / wrong, duplicate or partial rows | 2,323,000 / 0 / 0 |
| Independently verified successful read operations | 180 |
| First successful read / write after the fault | 298.729 / 298.976 s |
| Longest successful-read / successful-write completion gap | 298.724 / 299.088 s |
| Effective ARC maximum before / after reboot / after recovery | 256 / 256 / 256 MiB |
| Replication: last complete snapshot / new promoted rows | 1,500,000 / 100,000 |
| Acknowledged tail absent on asynchronous standby / recovered on fenced source | 30,000 / 30,000 |
| Final promoted rows / missing replicated or promoted ACKs | 1,600,000 / 0 |
| First promoted write / recovery plus ten batches | 28.534 / 29.327 s |
| Final full body checks across four exported tables/copies | 7,453,000 |
| Retained initial empty generation | native table inventory verified empty |

The 7,453,000 checks include replicated copies, not that many unique logical IDs.
The fenced source independently verified **1,530,000** bodies, including the
unreplicated tail. No divergent-copy merge or failback was attempted. The mirror
returned ONLINE after replacement and scrub with zero reported data errors.

| Client operation latency | Count | p50 / p99 |
|---|---:|---:|
| Baseline successful 1,000-row batch operations | 97 | 87.930 / 524.581 ms |
| Recovered successful batch operations | 83 | 68.905 / 201.769 ms |
| Baseline successful read operations | 55 | 52.694 / 552.621 ms |
| Recovered successful read operations | 48 | 42.524 / 142.993 ms |
| Fault-phase ambiguous batch operations | 691 | 41.550 ms / 53.987 s |
| Fault-phase failed reads | 373 | 32.241 / 204.769 ms |

One operation invoked before injection completed ambiguously afterward and is
attributed to baseline by invocation time. Native recovery recorded another
143 successful / 502 ambiguous batches and 77 successful / 264 failed reads.
Recovered observations had no failures. Attempts include client creation and
error cleanup; batch latency is not per-row latency. Read operations combine
historical and recent point values. Small successful phase counts make p99 near
a maximum; they do not establish a production tail.

The pinned [Quack request implementation](https://github.com/duckdb/duckdb-quack/blob/c1548111c1bfd16207e22fd3cb7e4bde1335b9d0/src/quack_client.cpp#L47-L95)
replaces the HTTP timeout parameter on every request with a
[constant of 86,400 seconds](https://github.com/duckdb/duckdb-quack/blob/c1548111c1bfd16207e22fd3cb7e4bde1335b9d0/src/include/quack_client.hpp#L79-L94).
It also sends a network DISCONNECT while closing a cached connection. Therefore
reading back `http_timeout=3` is **not an effective three-second Quack deadline**.
The longest observed ambiguous writer attempt was **75.058 s**. Setup, query/fetch
and cleanup spans were not separated; the source explains the ineffective setting,
not which span consumed those 75 seconds. Hard client-deadline qualification is
explicitly false in the report.

| Complete matrix resource/storage measurements | Primary | Standby | Controller |
|---|---:|---:|---:|
| Observed CPU core-seconds | 1,943.514 | 1,008.887 | 297.965 |
| Mean CPU, one core = 100% | 129.985% | 96.877% | 27.355% |
| Sample mean / maximum charged memory (MiB) | 2,190.705 / 2,851.355 | 1,878.637 / 2,201.973 | 155.765 / 553.555 |
| Peak allocated VM qcow files (MiB) | 1,179.789 | 360.398 | no VM disk files |
| Virtual-device read / write bytes | 847,982,592 / 1,330,825,216 | 306,067,968 / 154,062,848 | unavailable |
| Read / write IOPS over observed intervals | 17.049 / 30.164 | 10.529 / 16.941 | unavailable |

CPU/I/O deltas exclude resets and missing intervals; stopped roles are unavailable,
not measured zero. Primary and standby I/O observation windows were 1,495.184 and
1,041.406 s. These virtual-device counters include boot, DB, SSH and export work;
they are not physical disk I/O or steady-state DB measurements. Controller
`/state` allocation is empty because it has no VM disks; its host result writes
are separate. Ignored fresh-run results occupied **618,034,565 logical bytes**
(**637,538,304 allocated file-block bytes**) at the post-lifecycle observation.

In the continuous case, settled native ZFS `logicalreferenced` / `referenced`
were **175,450,112 / 118,796,288 bytes**, `compression=lz4`, ratio **1.48**.
This dataset includes the legacy numeric table plus the live table, DB and WAL.
The live-only CSV was **218,674,842 bytes**; off-path gzip1 produced
**108,600,676 bytes**, ratio **2.014**, in **1.422 s wall / 1.388 s CPU**.
These compression layers have different inputs and accounting boundaries.

The original environment headers remain intact. Dated addenda record sibling
offline SDK jobs at 1 CPU / 2 GiB and an image build at 2 CPUs / 4 GiB during
parts of the run; no competing SUT workload ran. Timing/resource observations are
diagnostics under that co-tenancy. No capacity winner is claimed.

## Earlier ARC checkpoint

[ARC_CHECKPOINT_RESULTS.json](ARC_CHECKPOINT_RESULTS.json) separately preserves
case `3ada336c8d5f4fbda0287c7aa97edcab`: **2,378,000** exact bodies, **377,000**
acknowledged mutation rows and **1,000** applied rows with ambiguous responses,
with zero missing acknowledged or wrong/partial/duplicate bodies. It re-exported
and verified **2,374,000** bodies from the preceding diagnostic case.

That preceding case (`a18f147a9e4347ea8f787de667cb3320`) recovered its bodies but
**failed the ARC configuration**: a runtime-only cap reset from 256 MiB to
760,590,336 bytes after reboot. The failed configuration and successful integrity
proof remain separate. The repaired setup persists the module option in initramfs,
enforces it before owner startup, and checks both the option and effective `c_max`
after reboot. The fresh complete workflow above verified both guests at 256 MiB.

Those older native volumes were created on November 22; their two UUID workloads
were carried into the first expanded suite with unchanged bytes/provenance.
November 14 journals belonged to earlier destroyed fixtures. Native GUID/table
inventory and full exports preserved every current-fixture body before cleanup;
no failed run was erased to turn it into a pass.

## Verifier failure and recovery

[VERIFIER_FAILURE_RESULTS.json](VERIFIER_FAILURE_RESULTS.json) records the first
expanded full-suite attempt. It passed disk replacement, continuous crash
recovery and standby promotion, then **failed** its final export deadline. A
Python CSV-rendering loop inside the software-emulated guest could not export
both historical tables in 600 seconds. Its timeout also left the remote export
process running after SSH exited. The exact PID, argv and process start time were
captured before termination; both partial files were preserved externally,
including one complete 224,159,329-byte CSV and a 42,443,328-byte prefix.
The original suite remains failed; it is not counted as a passing lifecycle.

The repaired verifier uses native DuckDB `COPY` with the same CRLF CSV dialect.
`make check-native-export` compares both legacy and payload-bearing shapes byte
for byte and rejects overwriting an earlier export. `make check-native-fencing`
creates an isolated heartbeat helper, triggers a real SSH timeout, fences its
exact process with a Linux pidfd, and checks the database owner did not change.
Future native-job errors preserve the original exception and the process-fencing
proof; a leftover helper blocks another export.

A failed final-render attempt can be retired for cleanup only after a new full
native archive verifies every acknowledged body and source tail, the old partial
bytes match prefixes of those independently verified exports, both database owners
and all native helpers are fenced, and fixture registrations and failed evidence
are hash-sealed. `make retire-failed-suite FAILED_SUITE=results/<run>/full-suite-<id>`
records **failed-but-archived**, never pass. It refuses earlier-stage failures.
This is a recovery operation after diagnosis, not part of the normal five-command
lifecycle. The subsequent fresh complete lifecycle is recorded above and in
[FULL_SUITE_RESULTS.json](FULL_SUITE_RESULTS.json); the earlier attempt remains failed.

## Limitations

| Component/scenario | Boundary |
|---|---|
| Mirrored pool | Survives one injected member fault; both virtual disks still share one physical Docker host/disk. A mirror is not a backup. Capacity is sparse and not reserved. |
| Retained-disk crash | Tests abrupt guest termination and WAL recovery, not loss of both mirror members, a failed storage controller or host power-loss guarantees. |
| Asynchronous replica | Snapshot lag can leave acknowledged rows absent after promotion. No zero-RPO guarantee, automatic fencing, failback or divergent-copy merge. |
| DuckDB clients | One Quack server owns the writable file; four processes are remote clients, not four independent file writers. Quack is beta in this pinned release. |
| Read-only clones | Recover/checkpoint a writable clone before publishing it read-only; do not query a dataset during receive. |
| Measurements | QEMU TCG emulation, synthetic rows and one shared host. Timings are not native ZFS/SSD or production DuckDB performance. |
| Legacy disk/replica controller | Finite writers stop on their first fault error; there is no continuously independent reader or fsynced pre-request intent in those legacy paths. Their final body/ACK checks and the new all-history export pass only the stated recovery cases; continuous availability/ambiguous-outcome production coverage remains incomplete. |

Remaining production coverage: isolating long client failure/cleanup spans and
establishing an explicit end-to-end deadline; forcing reads from the new mirror leg by failing
the original survivor after repair; disk latency/full; simultaneous mirror-member loss;
network partitions and asymmetric reachability, prolonged resource pressure,
rolling upgrades, independent backup/restore and failback are **unrun** here.
So are update/delete/transaction-isolation workloads, repeated cold/warm capacity
comparisons and continuous reader/intent coverage for the legacy disk/replica
paths. Asynchronous promotion is manual and fenced; this is not automatic HA or
a zero-RPO design. A final integrity pass does not fill those coverage gaps.

## Test sequence and measurements

1. Four clients seed half the configured rows. Checkpoint, send a baseline and
   validate an independent clone.
2. Continue concurrent appends through three-quarters of the configured IDs.
   Capture and replicate a live snapshot while clients are active; recover its
   DB/WAL on a clone and verify unique IDs, values and complete committed batches.
3. Checkpoint a new snapshot. Interrupt its incremental stream after 65,536 bytes,
   require a receive resume token, resume the stream and verify the result.
4. Start more concurrent appends without sending another snapshot. After at least
   two batches acknowledge, power-cut the primary VM using QMP. Confirm its
   endpoint is unreachable before starting the standby owner.
5. Compare every externally acknowledged ID range against the promoted database.
   Report missing acknowledgements, failed/ambiguous requests, duplicate IDs,
   malformed rows and partial batches separately. A deliberately unreplicated
   tail is expected to be absent on the promoted standby.
6. Commit ten more batches on the promoted owner and verify them. Measure first
   recovered write, recovery plus ten batches, replication bytes/times, and batch
   latency percentiles.

This demonstrates a real RPO window: an asynchronously replicated standby can
lack acknowledged writes. Those rows are not declared permanently destroyed;
the fenced old disk may still contain them and requires explicit reconciliation.
No automatic failback or merging of divergent copies is attempted. The post-promotion
`make up` guard was tested and refused to restart the stale primary. The complete
lifecycle removed this project's containers, volumes, network, image tag and keys.

Raw reports and per-client journals are ignored under `results/`. Reports below come from completed live runs.

## Measured recovery results

2026-11-14, Apple M4 Pro, ARM64 Docker VM with 11 CPUs / 24.42 GiB RAM. Each
QEMU guest runs TCG software emulation with 2 vCPUs / 1536 MiB, inside a
2-CPU / 3-GiB container. Controller: 2 CPUs / 1 GiB. DuckDB owner:
`memory_limit='512MB'`, 2 threads; clients: `256MB`, 2 threads each. ARC: 256 MiB.
Other example deployments were stopped for these tests. Guest kernel
6.8.0-147-generic; ZFS userspace/module 2.2.2-0ubuntu9.5; DuckDB 1.5.5;
Quack `c154811`. Raw reports/journals: `results/reliability/`.

These are three separate tests on mirrored pools, with normal database sync
semantics. Four clients, 10,000-row batches and a 1-second deliberate batch delay
keep writes active during faults. The crash tests intentionally cut their
2-million-ID workload before completion. No failed request is blindly replayed.

| Disk replacement | Observed |
|---|---:|
| Pool size (ZFS accounting, before reservations) | 16,642,998,272 bytes |
| Mirror state | ONLINE → DEGRADED/FAULTED → ONLINE |
| Additional acknowledged rows proven while degraded | ≥ 20,000 |
| Final rows / distinct IDs | 2,000,000 / 2,000,000 |
| Missing acknowledgements / invalid rows / duplicates | 0 / 0 / 0 |
| Replacement command and wait, including SSH | 9.652 s |
| Scrub plus healthy-state verification, including SSH | 9.646 s |
| Final scrub checksum/data errors | 0 |

| Retained-disk VM crash | Observed |
|---|---:|
| WAL bytes before abrupt termination | 38,954,876 bytes |
| Acknowledged rows before cut / recovered rows | 1,080,000 / 1,080,000 |
| Missing acknowledgements / invalid rows / duplicates | 0 / 0 / 0 |
| Ambiguous failed requests, recorded without replay | 4 |
| New rows committed after recovery | 100,000 |
| New missing acknowledgements | 0 |
| First recovered write, including guest reboot and health wait | 239.733 s |

| Asynchronous standby promotion | Observed |
|---|---:|
| Seed / live-snapshot recovered rows | 1,000,000 / 1,110,000 |
| Last complete replicated rows | 1,500,000 |
| Replicated seed/live acknowledgements missing | 0 |
| Deliberately unreplicated acknowledgements absent on standby | 30,000 |
| Ambiguous failed requests, recorded without replay | 4 |
| New rows committed after promotion / missing new acknowledgements | 100,000 / 0 |
| Final rows / distinct IDs before benchmark | 1,600,000 / 1,600,000 |
| First recovered write after fencing | 12.745 s |
| Recovery plus ten committed batches | 13.383 s |
| Full snapshot stream | 10,294,016 bytes / 14.527 s |
| Live incremental stream | 4,476,520 bytes / 7.324 s |
| Interrupted stream / resumed stream | 65,536 / 6,052,936 bytes |

All recovered clones had complete committed batches and correct deterministic
values; two independent read-only readers agreed on each clone. Receive resume
required a real token after interruption. Stream byte counts exclude SSH framing.
Promotion excludes rebooting a guest; retained-disk recovery includes it. Missing
unreplicated rows may still exist on the fenced old primary and require explicit
reconciliation. The mirror and local crash tests lost zero acknowledged rows.

## Historical benchmark (2026-11-14)

This older run did not continuously collect CPU, memory, block-I/O and compression
on the benchmark timeline. Its numbers remain historical diagnostics, not a
qualified holistic performance comparison. The legacy `benchmark` target has not
been requalified with its own continuous resource collector or sustained matrix.
The new recovery measurements above cover those resources; no new capacity winner
is claimed.

Run `make benchmark` once after the successful complete `make test`. Same hardware,
versions and caps as above; the standby is the active owner. Four clients issue
server-generated SQL with no deliberate batch delay. This is a TCG-emulated local
lab, not native-machine DuckDB/ZFS or application-payload throughput.

| Ingestion | Observed |
|---|---:|
| Additional rows / final distinct rows | 200,000 / 1,800,000 |
| Wall time including client startup and external journal fsync | 2.101 s |
| Generated rows per second | 95,178 rows/s |
| Batch p50 / p99, excluding client startup and journal fsync | 255.905 / 488.841 ms |
| New missing acknowledgements / invalid rows / duplicates | 0 / 0 / 0 |

Warm-query run: 2 warmups plus 20 measured calls per query, including Quack round
trips; each count/sum is verified. These synthetic, compressible data fit memory.
With 20 samples, p99 is the maximum and cannot describe a production tail.

| Query | Matching rows | p50 (ms) | p99 (ms) |
|---|---:|---:|---:|
| aggregate | 1,800,000 | 149.585 | 400.174 |
| customer | 180 | 65.794 | 160.598 |

Raw timed samples: `results/reliability/benchmark.json`. The benchmark is a one-shot append after the replica test; a clean lifecycle resets it.

## Versions and failure domains

DuckDB 1.5.5 and its Quack extension are baked into the image and copied into the
guests offline. Quack is beta in this version. Ubuntu 24.04 ARM cloud image
20261017 is SHA-256 verified in the Dockerfile and includes the matching ZFS
kernel modules. ZFS userspace packages 2.2.2-0ubuntu9.5 and the Python runtime
are baked into the Docker image and installed offline in the guests. The
Quack and HTTPFS extension bytes are SHA-256 checked during image build; a
changed upstream artifact fails that build rather than silently changing the lab. `versions-<uuid>.json` records the actual
kernel/module and database versions used.

Each QEMU container has 2 CPUs/3 GiB; each guest has 2 virtual CPUs/1.5 GiB. DuckDB
server memory is `512MB`; clients have `256MB` engine limits; the controller has
2 CPUs/1 GiB. QEMU uses TCG software emulation even on the ARM64 host. Timings are
lab recovery observations, not native-machine performance benchmarks. All guest
images and pools still share one Docker host/disk; this is not physical disk-loss
or multi-host HA evidence.

No host ports are published. SSH, Quack and QMP use 2222, 9494 and 10099 inside
the private Compose network. SSH keys are generated locally and ignored. The
Quack token is a public local-demo value; this is not an internet-facing service.

## Sources

- [DuckDB concurrency](https://duckdb.org/docs/1.5/connect/concurrency) and [Quack protocol](https://duckdb.org/docs/1.5/quack/overview).
- [DuckDB checkpoint semantics](https://duckdb.org/docs/1.5/sql/statements/checkpoint).
- [ZFS send](https://openzfs.github.io/openzfs-docs/man/master/8/zfs-send.8.html), [receive and resumable streams](https://openzfs.github.io/openzfs-docs/man/master/8/zfs-receive.8.html).
- [OpenZFS 2.2 disk faulting](https://openzfs.github.io/openzfs-docs/man/v2.2/8/zpool-offline.8.html), [replacement and wait](https://openzfs.github.io/openzfs-docs/man/v2.2/8/zpool-replace.8.html), [scrub and wait](https://openzfs.github.io/openzfs-docs/man/v2.2/8/zpool-scrub.8.html).
- [QEMU ARM virtual platform](https://www.qemu.org/docs/master/system/arm/virt.html).

Inspected source: DuckDB v1.5.5 `local_file_system.cpp` (file locking) and
`storage_manager.cpp` (checkpoint/WAL recovery), installed Quack revision
`c154811` insert and transaction implementations, OpenZFS `zfs_main.c` (send/receive,
resumable receive), and pinned OpenZFS zfs-2.2.2 `zpool_main.c` (`zpool_do_offline`,
`zpool_do_attach_or_replace`, `zpool_do_scrub`: persistent fault and completion waits). Fencing, client journals and role selection are application
logic supplied by this example.
