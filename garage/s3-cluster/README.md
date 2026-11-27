# Garage: replicated S3 and permanent disk loss

The current image pin is Garage **2.6.0**. The historical benchmarks and recovery
results below were measured on **2.5.0** and remain labeled as such; they do not
become 2.6.0 performance claims. See the [current multipart recovery follow-up](docs/MULTIPART_RECOVERY_PLAN.md).

Nine Garage storage nodes distribute three copies across three logical
zones. Separate metadata/data volumes make permanent data-disk replacement
observable. SQLite metadata, consistent quorum mode and data fsync are explicit.

## Quick start

1. `make docker-up` — start nine nodes and the S3 load balancer.
2. `make up` — build the client, configure placement/credentials and seed data.
3. `make test` — verify S3 semantics and complete object bodies.
4. `make down` — delete the example bucket and its objects; keep the cluster running.
5. `make docker-down` — destroy its network/volumes and remove its client image.

Requires Docker Compose, Python 3.11+ and approximately 10 GiB available Docker disk
space. S3 is internal at `s3:9000`; the test credentials are local fixtures, not
production secrets. No host ports or host devices are needed.

## Setup

| Component | Count | Limits / behavior |
|---|---:|---|
| Garage `dxflrs/garage:v2.6.0` (digest pinned) | 9 | 1 CPU, 768 MiB each; three nodes per logical zone |
| HAProxy `3.2.8-alpine` | 1 | 0.5 CPU, 128 MiB; nine S3 frontends |
| Docker-built Boto3 client | 1 | 2 CPUs, 2 GiB; SDK 1.44.3, SigV4/path addressing |
| Metadata/data disks | 18 | Separate named volumes; shared physical Docker filesystem |
| Data/sharded metadata | 3 | Consistent mode, sharded-metadata quorum 2; block writes acknowledge 2; one copy per logical zone |
| Data durability | — | `data_fsync=true`, uncompressed native blocks |
| Metadata | — | SQLite WAL, `metadata_fsync=true`; native hourly snapshots |
| Native RPC timeout | — | 5 s for this local LAN; upstream default is 300 s |

Small full-copy metadata uses all nine members; its consistent-mode majority
quorum is five. Immutable block reads need one valid content-hash copy. The
three-copy/factor-two quorum description does not apply to every metadata table.

Logical capacity is 4 decimal GB per node. This is placement capacity, **not** a
Docker disk quota. The containers and zones share one physical computer. The
S3 gateway is a lab routing component; production gateway redundancy, TLS,
credential rotation and independently powered disks are separate deployment work.

`OBJECTS=10000` small 4 KiB objects and `LARGE_OBJECTS=128` one-MiB objects are
Make variables. The base suite verifies 10,133 final objects / 210,833,408 bytes,
including a 32 MiB multipart object. It also checks HEAD, a 100-byte range,
Unicode keys, copy, four overwrite versions with immediate reads, delete,
multipart abort, pagination, and invalid Content-MD5 rejection. Eleven shared negative
client controls reject corrupted/truncated/stale data, unavailable responses,
missing acknowledgements and malformed pagination.

## Fresh 2.6.0 qualification — 2026-11-27

The digest-pinned ARM64 binary reported `garage v2.6.0`. A new, smaller
correctness/recovery fixture used 1,000 small objects and eight 1 MiB objects;
this does not replace the historical 10,000-object performance profile.

| Case | Measured outcome |
|---|---|
| Complete API suite | 1,013 objects / 48,140,288 bytes; healthy 32 MiB multipart completion and abort included; zero wrong/missing data |
| Native owner crash | 30.008 s outage of `garage0`, proved exit 137; 197 PUT ACKs, one ambiguous PUT and 63 successful GETs during outage |
| Entire live case | 863 ACKs / one ambiguous attempt; 867 successful GETs / zero GET errors; longest completion gap 5.146 s |
| Final reconciliation | 1,876 objects / 76,419,072 bytes; every ACK present/correct; the non-ACK object absent |
| Exact physical owners | 5,754 required copies of 1,918 blocks verified over all 256 partitions; zero missing, misplaced or corrupt required copies |
| Resource diagnostics | Sampled aggregate charged-memory peak 530,042,880 bytes; 29.869 CPU core-seconds, 372,736 disk-read bytes and 1,538,437,120 disk-write bytes across observed intervals |
| Storage snapshot | Native data directories: 235,549,248 apparent / 236,077,056 allocated bytes; metadata reported separately |
| Lifecycle | Five commands passed; owned cluster containers, volumes, network and tools tag removed; witnesses retained |

[Recovery/phase results](reports/GARAGE_2_6_RECOVERY.json) and
[resource samples summary](reports/GARAGE_2_6_RESOURCES.json) retain the native
copy barrier, CPU utilization, image/limits and measurement gaps. Native repair
was explicitly invoked; this does not prove completely automatic all-copy repair.
The original `G010lh` decoder passed on all nine 2.6.0 metadata disks, with native
partition/ring lookup reviewed against pinned source. Unknown/transitional layouts
still fail closed.

Initial setup and seed started before resource sampling; these are observed-window
diagnostics, **not** full-lifecycle totals or a repeated comparative benchmark.
Docker exposed I/O byte counters but no operation counts or device latency. Reads
were warm/shared-cache, not verified cold. Compression is disabled (native body
ratio 1.0); replication, deduplication, metadata, GC and allocation are separate costs.
No 2.5.0-versus-2.6.0 performance winner is established. The original live-crash trial did
not inject a fault inside multipart completion; the separate bounded follow-up
below records those tests without changing the original trial's scope.

To observe another case, use `RESULTS_DIR=... python3 scripts/observe.py failover`
(or `verify`) with the same local fixture. `scripts/storage_usage.py` runs inside
the read-only audit container; `scripts/resource_report.py` summarizes raw Docker
counters and accounts for counter resets across container restart. No host cloud
credentials or host filesystem storage paths are used.

## Multipart publication boundaries — 2026-11-27

After the five-command setup and API test, `make multipart-failure` runs a healthy
raw-HTTP control, serving-node loss with an incomplete completion body, deliberate
response disposal and abort visibility. It creates its own case directory within
the existing fixture; no new cluster or sibling example state is required.
[Workload/build ownership](multipart-workload/) · [test design](docs/MULTIPART_RECOVERY_PLAN.md)
· [recovery results](reports/MULTIPART_RECOVERY.json) · [resource diagnostics](reports/MULTIPART_RESOURCES.json).

- Each multipart object used four **8 MiB** parts. The controller verified the
  connected owned peer, application source hash, **197/395 request bytes sent**
  and the target's SIGKILL exit **137**. All four part ETags/sizes survived;
  a fresh client completed the **same upload ID** and verified all **32 MiB**.
- Discarding the completion response after headers produced **one ambiguous
  publication**, fully readable by a fresh client without replay. It remains
  separate from the **two multipart client ACKs**. Abort removed its upload from
  listings and kept its final key absent; physical reclamation is unrun.
- Independent background traffic recorded **379 PUT ACKs**, **zero ambiguous
  ordinary PUTs**, and **715 successful GETs / zero errors or wrong bodies**.
  The longest successful-GET completion gap was **1.071 s**.
- Final readback verified **1,395 objects / 161,222,656 bytes**. The native audit
  checked **4,626 required copies of 1,542 blocks** over **256 partitions**, with
  no missing, misplaced or corrupt required copies. This includes the independently
  observed publication whose completion response was discarded.
- Observed diagnostics: **869,531,648 bytes** aggregate charged-memory peak,
  **22.703 CPU core-seconds** and **1,208,143,872 disk-write bytes**. CPU utilization,
  disk reads, logical/allocated storage and collection gaps remain in the reports.
  No cold-cache, throughput-ranking or device I/O latency/count claim is made.

This tests an incomplete request and an application-discarded response, **not** an
internal metadata-commit crash or a proven network loss before server publication.
Regular writes/reads continued through native repair; the fixed ACK prefix regained
all copies before new progress was required. A separate reader remained live during
final tail reconciliation. Four protocol controls reject false ACKs and unowned
endpoints; a history control proves a discarded response cannot become an ACK.

Cleanup also exposed a negative result: the first `make down` saw a nonempty
listing immediately after batch deletion and failed. A later fresh client found
**zero objects and zero pending uploads**; an explicit second `make down` and owned
Docker teardown succeeded. The failed check remains preserved. Its cause and
visibility bound have not been isolated; do not claim a consistency violation or
hide it with an automatic retry.

## Live production tests

`make test-production` serially runs the API suite, an actual data-owner crash,
permanent owned data-volume replacement, an S3-router crash and full verification.
Use `make failover`, `make disk-failure`, `make gateway-failure`, `make verify`,
`make repair`, `make cold-reopen` and `make report` separately when investigating. The default
`RESULTS_DIR=results/recovery` contains a unique directory for every fault run.
`FAULT_DURATION=900` is a safety ceiling (60–1,800 s allowed), not a load duration
or a substitute for native recovery. A traffic process exhausting that ceiling
fails the test.

Two **separate Compose containers** generate paced PUTs and independent GETs.
Each sends one request at a time with a 50 ms pause. PUTs have unique keys and
32 KiB bodies, one SDK attempt and fsynced attempt/outcome records. GETs alternate
historical ACK bodies and current ACK bodies; a blocked PUT cannot stop read
observation. Full SHA-256, length and application version are checked. Wrong
successful reads fail immediately. A 404 for an immutable ACK also fails the
consistent-read contract; transport errors/503 remain availability observations. SDK write errors are conservatively treated
as possibly committed, including definite connection failures; raw errors and
latencies distinguish them. This is an upper bound on ambiguous commits, not a
claim every failed request reached the server. No mutation is blindly replayed.

The existing maintained Boto3 compatibility/checksum harness is retained, so this
Python SDK workload is an explicit exception to the usual Go/Rust/C++ application
preference. Controllers and native filesystem audits use Python's standard
library; a pinned MessagePack decoder handles the native layout format instead
of implementing a codec.

The controller selects a node that owns a required live block; `FAULT_TARGET`
may select another allowlisted owner. Native SIGKILL exit 137 proves the crash.
For permanent data loss it stops/removes the exact owner, proves its old ID absent
in a healthy runtime listing, deletes only the labelled data volume, and removes
only the documented `/meta/data_layout` marker on its retained metadata disk.
The SQLite database and node identity remain intact. All nine unrelated
container/main-process IDs, start times, PIDs and restart counts must be unchanged.
The single HAProxy router is a deliberately tested availability boundary; nine
native S3 frontends do not make this one routing process redundant.

### Recovery barrier

Membership is checked on all nine nodes: all 256 partitions must have their native
quorum and every configured peer connected. The controller then captures a
**fixed prefix of live acknowledged writes**, with writers/readers still running.
It waits for metadata insert/Merkle work, launches native metadata full sync,
waits for sync/index workers, launches native block repair and checks every
required block of the old history and live prefix on **all exact native owners**.
The writer continues throughout those operations and must make further progress
after the all-copy barrier. It stops only afterward; a separately labelled final
reconciliation also repairs and verifies the subsequent write tail while the
independent reader remains live.

Native full-sync success is a quorum result. An idle worker, zero foreground
queue or a healthy membership API does **not** establish all-copy completion.
If the fixed prefix still lacks copies, the controller may launch another native
metadata sweep, at most eight, no more often than every 30 s and only after prior
sync/index workers become idle. Each sweep is recorded with timestamps and live
roles. These are explicit native recovery assists, not application PUT retries
or a claim of completely automatic recovery. The finite prefix prevents new
traffic from moving the recovery goal indefinitely.

Garage also keeps legitimate delayed garbage collection in its resync queue
(`BLOCK_GC_DELAY + 10 s = 610 s`). The final gate requires metadata work to finish,
no current resync errors and every live expected physical copy; it records queued
GC instead of pretending a future GC timer is missing acknowledged data. Native
repair launches run locally on each node with checked exits, because an aggregate
RPC can print a peer error while returning success. The server logs, worker and
queue snapshots, raw request events and separate live/final repair journals are
retained. Latency and error rates are separated by actual operation phase;
`reconcile` runs with the reader and a stopped writer, so its PUT rate is not a
steady-state throughput number.

### Exact placement and bytes

The read-only auditor decodes the pinned native `G010lh` layout on **all nine
metadata disks** and requires agreement. It uses Garage's stored
`ring_assignment_data` / `node_id_vec` table and the native top-eight-hash-bit
partition lookup; it does not recompute placement. Unknown markers, changed
membership, transitional multi-version layouts or a non-three-copy ring fail
closed. This private, version-pinned format is not a stable public API; an upgrade
requires requalification of the decoder.

For each acknowledged object's required blocks, the auditor requires the exact
native storage nodes, correct `/data/<hash[0:2]>/<hash[2:4]>/<hash>` path and full
BLAKE2b-512/32-byte identity. One wrong node in the right zone fails even if there
are still three zone copies; that case has a negative unit control. The suite
also verifies complete S3 bodies against the external SHA-256 witness. Native
metadata worker/queue checks are not independent byte-for-byte verification of
every SQLite metadata row. The auditor is deliberately scoped to uncompressed,
single-data-directory Garage 2.5.0 and the fresh 2.6.0 baseline, with the fixed nine-node layout used here.

### Lifecycle and remaining boundaries

After initial node identities are discovered, configuration includes the eight
other `bootstrap_peers` on every node. The initialization applies that native
configuration before seeding application data; later whole-stack starts can
reconnect without manually rebuilding membership.

The example's local `s3-workload` and `benchmark.py` links point to tracked source;
`witness_archive.py` links to the shared evidence-preservation helper. `make build`
materializes regular files in ignored `.build/tools` using hard links where
possible, copies local modules and builds the Docker image from that local
context. Nothing depends on another example's running services or generated
state. Materialized files and all raw results remain untracked.

Initial seeding also runs explicit native metadata/block repair and an exact-copy
barrier before marking the fixture ready. The first fresh seed exposed 239 blocks
without their third native copy despite idle/error-free native queues. Its startup
remains failed; `make recover-bootstrap` resumes only a diagnosed, fully seeded
`initializing` fixture, repairs/verifies it without replaying PUTs, and keeps the
original manifest. Partial seeds fail closed and require diagnosis.

Repeated `make up` verifies retained state. `make docker-down` records a matching
fixture-ID proof after confirming zero owned containers/volumes/networks/build
tags. On the next complete lifecycle, `up` archives the retired witness and failed
histories under ignored `previous-runs/` before creating a new fixture. Unexplained
storage loss or an unmatched teardown proof fails closed. Use the complete five
commands for a fresh dataset; `down` alone intentionally removes the workload.
`make cold-reopen` separately stops/starts the whole retained stack and checks
persistence without a live-workload/availability claim. Docker VM filesystem
caches remain warm; this is not a cold-query benchmark.

Peer partitions, native quorum loss, topology expansion/shrink, live resource
changes, rolling upgrades, metadata-disk loss, bounded pressure, physical EIO or
power loss and independent backup restore remain **unrun**. Bootstrap configuration
restarts are not live rolling-maintenance tests. The nine logical failure domains
and external journals still share one physical host. Local correctness and fault
passes do not establish independent-host durability or production certification.

## Recovery follow-up (2026-11-16)

Apple M4 Pro, native ARM64 Docker VM with 11 CPUs / 24.42 GiB RAM; the
nine-node storage/router budget is 9.5 CPUs / 6.875 GiB. During faults, two
independent traffic containers add up to 4 CPUs / 4 GiB and the read-only auditor
adds 2 CPUs / 2 GiB; CPU limits therefore oversubscribe this one VM. These are
local observations, not independent-host durability or normalized comparisons.

The retained main fixture reconciled **13,817 ACK objects across eight histories**
from 14,666 attempts, including failed and externally assisted investigations.
The 849 non-ACK attempts were all absent at final reconciliation; none is silently
counted as a successful write.
Full S3 readback verified **44,718 objects / 2,356,121,600 bytes**; the exact-owner
audit verified **139,248 required native block copies across all 256 partitions**.
No missing ACKs, corrupt bodies, stale versions, unknown keys, missing required
copies or wrong native owners remained. Nine unrelated container/main-process
identities were unchanged in every qualified fault.

| Controller-managed case | PUT ACK / non-ACK | GET success / error | ACK / GET during 30 s outage | Longest successful-GET gap | Fixed live-prefix copy wait |
|---|---:|---:|---:|---:|---:|
| Native process crash | 1,209 / 2 | 1,652 / 0 | 194 / 36 | 5.097 s | 50.360 s |
| Empty owned data disk | 3,783 / 1 | 4,878 / 1 | 202 / 36 | 12.130 s | 54.530 s |
| Single S3 router crash | 338 / 442 | 1,091 / 442 | 0 / 0 | 30.478 s | 3.833 s |
| Re-crash rebuilt native member | 1,209 / 1 | 1,765 / 1 | 185 / 44 | 12.179 s | 47.949 s |

The native process/disk cases needed an additional recorded metadata sweep while
both traffic roles remained live. Prefix wait starts after the initial native
repair launch and is not total fault recovery time. SDK timeouts were about
12 s; successful-read gaps and non-ACK counts disclose the availability cost.
The router is a single point of failure: zero useful progress during its outage
is an observed limitation, not continuous availability.

An earlier process test remains **failed**: successful S3 readback and idle native
workers coexisted with 41 required blocks missing on one native owner. A checked
metadata/block sweep restored the copies without replaying PUTs. Two subsequent
investigations used explicit outside-controller repair assists and remain labelled
assisted; neither qualifies unattended controller recovery. The table above uses
later controller-managed runs. No failed history was discarded or reseeded.

A complete stop/start first exposed missing native bootstrap peers: each node
knew only itself despite retained storage. Applying the peer configuration to the
same dataset restored membership and every required body/copy. That cold attempt
remains failed, as does a later evidence-collector filename failure after the
storage checks passed. The corrected full-stack cold reopen passed in **72.320 s**,
retaining all ten container IDs and restarting every process. Retained `make up`
then verified the same dataset in **67.847 s**.

The first follow-up benchmark completed **41,536 requests, zero failures** before
the peer-discovery fix: 1 MiB / 16 clients achieved **151.27 PUT MiB/s / 513.72 warm
GET MiB/s**. It uses the 5 s RPC profile but is not a measurement of the final
bootstrap configuration. Its raw output remains under
`results/production-20261116/`; failed statuses, manual assists, native worker
snapshots and request journals remain alongside it. `make report` reads these
saved witnesses after cleanup and keeps failed/assisted labels.

The corrected fresh setup passed in **112.421 s**, followed by the API suite
(**12.585 s**) and full S3/native verification (**16.497 s**). A matching fixture-ID
teardown proof allowed the next `up` to archive the previous 10,133-object oracle,
and reconciliation without overwriting them; the failed-startup log remains
alongside the archive. The final
`make unit-test` passed **29 controls** (20 Docker SDK/witness/placement/read tests
and nine host bootstrap/recovery/teardown/configuration tests).

### Final-configuration live matrix

The corrected fresh fixture completed `make test-production` in **438.736 s**.
All three cases used the final controller source and peer configuration; neither
manual repair nor PUT replay was used outside the controller.

| Case | PUT ACK / non-ACK | GET success / error | Outage ACK / GET | Longest successful-GET gap | Fixed-prefix native-copy wait |
|---|---:|---:|---:|---:|---:|
| Native process | 1,217 / 1 | 1,410 / 1 | 194 / 11 | 15.136 s | 48.286 s |
| Empty owned data disk | 1,530 / 1 | 1,958 / 0 | 197 / 69 | 5.084 s | 2.064 s |
| Single router | 288 / 400 | 795 / 449 | 0 / 0 | 30.473 s | 2.166 s |

Before the benchmark, all **3,035 ACK objects across three histories** survived;
all 402 non-ACK attempts were absent. Full readback verified **13,168 objects /
310,284,288 bytes**, and all **39,990 required native copies** matched their native
owners. All 256 partitions were represented and all nine unrelated process
identities stayed unchanged per case. The process case used one additional
recorded live metadata sweep; the disk and router prefix audits needed none.
`make cold-reopen` separately checks persistence of the rebuilt disk.

## Benchmark

Measured 2026-11-16 on the final bootstrap/recovery configuration: Apple M4 Pro,
Docker 29.5.3 aarch64, 11 CPUs / 24.42 GiB RAM. Store/router caps total 9.5 CPUs /
6.875 GiB; the benchmark client adds 2 CPUs / 2 GiB. No other SUT ran. This is one
post-recovery run; GET immediately follows PUT with warm caches. Signing, transfer
and full GET hashing are timed; body generation is excluded. No mutation retries.

| Object | Clients | PUT ops/s | PUT MiB/s | PUT p50/p99 ms | GET ops/s | GET MiB/s | GET p50/p99 ms |
|---|---:|---:|---:|---|---:|---:|---|
| 4 KiB | 1 | 29.63 | 0.12 | 43.57 / 93.90 | 140.09 | 0.55 | 1.82 / 43.82 |
| 4 KiB | 16 | 230.38 | 0.90 | 53.07 / 580.70 | 1234.81 | 4.82 | 9.34 / 50.11 |
| 1 MiB | 1 | 21.93 | 21.93 | 46.15 / 132.61 | 68.96 | 68.96 | 6.71 / 49.12 |
| 1 MiB | 16 | 155.14 | 155.14 | 96.26 / 181.56 | 458.55 | 458.55 | 25.65 / 92.45 |
| 4 MiB | 1 | 11.51 | 46.04 | 86.26 / 164.02 | 35.52 | 142.06 | 16.75 / 98.45 |
| 4 MiB | 16 | 57.62 | 230.46 | 267.84 / 445.17 | 220.48 | 881.92 | 54.75 / 147.94 |

**41,536 timed requests, zero request failures; 499.815 s total command time.**
The read-only audit immediately after the benchmark found all three native copies
of every required block: **35,634 required block hashes, zero missing, misplaced
or corrupt blocks**, before the explicit post-benchmark repair.
Raw latency distributions, resource inventory and externally witnessed bodies are
under `results/lifecycle-20261116/`; the next fresh lifecycle preserves their
oracles under `previous-runs/`. These caps/topology differ from other stores;
this is not a normalized product ranking.

Post-benchmark full readback checked **33,936 objects / 2,002,817,024 bytes**;
all four reconciliation counters stayed zero. The native audit verified
**106,902 required block copies**, zero missing/misplaced/corrupt blocks. The
subsequent full-stack stop/start passed in **57.673 s**, retaining all ten
container IDs and checking every process restarted, all retained volume births,
all object bodies and the same required native copies. This includes the rebuilt
data disk. The offline saved-evidence report also passed before cleanup.

Both corrected fresh-volume lifecycles passed on the same results path. The
second clean startup took **112.937 s**, its API suite **12.520 s**, full native/S3
verification **16.018 s**, and bucket cleanup **47.215 s**. It checked another
**10,133 objects / 210,833,408 bytes** and **30,885 required native block copies**.
The two retired fixture UUIDs retain their respective 10,133-object and
33,936-object manifests/reconciliations plus teardown proofs; the archived
benchmark/fault dataset also passed `make report` after recreation. All **four**
cleanup passes (retained main, diagnosed first fresh fixture and both corrected
fresh fixtures) left **zero owned containers, volumes, networks or build tags**.
The first failed seed and both earlier failed cold attempts remain failures.

## Previous recovery experiments (2026-11-15)

Run after `make test`, optionally after `make benchmark`:

| Command | Injection and evidence |
|---|---|
| `make failover` | SIGKILL one native process for about 30 s; persistent disks retained |
| `make disk-failure` | Kill `garage0`, remove its owned data volume, attach an empty replacement; metadata and node identity retained |
| `make repair` | Current target: checked metadata/block repair followed by full S3/native verification |
| `make status` | Native membership and Compose health |

The producer does not pause for recovery. Each PUT has a unique body/key, an
externally fsynced attempt record and a separate ACK record. The SDK makes one
attempt; ambiguous writes are not blindly replayed. Subsequent experiments retain
all previous attempts/ACKs. Final listing and full SHA-256 GET reconciliation
reject missing ACKs, unknown keys, corrupt bytes and stale application versions.

A read-only audit checks every physical block's native BLAKE2b identity and
requires each live expected block in all three zones. It uses standard-library
hashing and the pinned native block format, not a custom placement algorithm.
Multipart part boundaries are respected. The audit also checks reconstructed
copies independently of successful reads from surviving nodes.

Disk replacement is a permanently lost/replaced Docker data volume. It is not a
kernel EIO injection. Ceph's companion example exercises actual guest SCSI removal.

Measured 2026-11-15; continuous 32 KiB writes, about 30 s outage per case:

| Failure | Attempts / ACKs | ACKs during outage | First ACK after fault | Ready after restart | Baseline replica recovery wait | Final verified objects |
|---|---:|---:|---:|---:|---:|---:|
| Process | 300 / 300 | 5 | 4.77 s | 5.75 s | 10.49 s | 37,434 |
| Empty data disk | 3,397 / 3,397 | 3 | 14.72 s | 5.81 s | 290.77 s | 40,831 |

Both cases: zero missing acknowledged objects, corrupt bodies, stale versions or
unknown keys. Disk recovery finished with 127,587 verified physical block copies
and zero missing zone replicas. Baseline recovery wait measures the native/audit
phase after restart, not total outage-to-recovery time; final complete reconciliation
and copy audit add time. Other local examples were active during recovery runs,
so these timings describe this shared-host run. Historical diagnostic attempts and
benchmark objects remain in the cumulative oracle. Final cleanup deleted 40,831
objects, the bucket, then all owned containers/volumes/network and the client image.
A separate fresh lifecycle also passed two complete 10,133-object suites, including
`down` → `up` bucket recreation on the same running storage cluster.

## Previous benchmark (2026-11-15)

Measured 2026-11-15, Apple M4 Pro, Docker VM aarch64, 11 CPUs / 24.42 GiB RAM.
One native-container run; caps above. No other SUT ran during this benchmark.
Unique incompressible objects prevent content-addressed deduplication shortcuts.
Preparation is outside the timer; signing, checksums, transfer and complete GET
verification are inside. GET immediately follows PUT and is warm: this is not a
cold physical-disk benchmark. The client and server share a Docker network.

| Object | Clients | PUT ops/s | PUT MiB/s | PUT p50/p99 ms | GET ops/s | GET MiB/s | GET p50/p99 ms |
|---|---:|---:|---:|---|---:|---:|---|
| 4 KiB | 1 | 29.30 | 0.11 | 43.61 / 96.50 | 139.26 | 0.54 | 1.80 / 44.43 |
| 4 KiB | 16 | 334.04 | 1.30 | 46.31 / 114.45 | 1,227.89 | 4.80 | 9.11 / 50.38 |
| 1 MiB | 1 | 23.90 | 23.90 | 48.37 / 101.00 | 70.61 | 70.61 | 6.49 / 52.49 |
| 1 MiB | 16 | 166.08 | 166.08 | 86.37 / 175.16 | 479.11 | 479.11 | 26.46 / 76.33 |
| 4 MiB | 1 | 12.16 | 48.63 | 83.67 / 158.08 | 32.81 | 131.25 | 16.72 / 140.56 |
| 4 MiB | 16 | 67.25 | 269.00 | 216.85 / 353.24 | 237.59 | 950.36 | 56.01 / 148.11 |

41,536 timed requests, zero benchmark request failures. Make/environment variables
`BENCH_SMALL_REQUESTS=10000`, `BENCH_REQUESTS=256` and `BENCH_LARGE_REQUESTS=128`
control counts **per operation/concurrency combination**. Benchmark prefixes must
be empty: use a clean lifecycle for another run rather than rewriting deduplicated
objects. The recorded steady-state run used the upstream 300 s internal RPC
timeout; the later fault profile sets 5 s. Those timeout configurations are
reported separately rather than claiming this table measured the changed profile.

Raw manifests, latency distributions, resource limits and fault journals are in
`results/verified/` (ignored). Only observed summaries belong in this README.

## Gotchas and limitations

- Garage 2.5.0 does not support S3 bucket versioning. The overwrite test uses an
  application metadata version; it does not claim S3 version-history support.
- `metadata_fsync=true` with SQLite uses WAL synchronous NORMAL. Do not infer
  simultaneous all-node power-loss durability from the name. Data fsync and a
  native metadata snapshot/backup strategy serve different purposes.
- An empty replacement disk fails Garage's saved disk-marker check. The native
  documented recovery removes `/meta/data_layout` while preserving the metadata
  DB and identity, then lets Garage create the new disk marker.
- Internal RPC timeout defaults to **300 s**. Missing block checks are scheduled
  after **twice** that timeout. Native block repair does not bring an already
  scheduled check forward. The original default-timeout run exceeded a 600 s
  audit deadline; successful reads were not accepted as complete recovery.
- This lab sets `rpc_timeout_msec=5000`. It is a local failure-detection choice,
  not a WAN sizing recommendation. Metadata-table convergence and physical block
  convergence must both be checked after a crash.
- `garage repair --all-nodes` can print an individual peer RPC error yet exit
  zero during reconnection. The harness launches checked local repairs per node
  and verifies physical replicas afterwards.
- Editing bind-mounted TOML and running `compose up` does not restart unchanged
  containers. The runtime-config helper detects a changed config digest and
  applies a rolling restart before a new fault experiment.
- Default block size `1M` is decimal 1,000,000 bytes: a one-MiB object has two
  blocks. Multipart ETags are not full-object checksums. Data compression is off
  in these measurements; production compression and WAN costs need separate runs.
- Three local logical zones do not demonstrate a real datacenter or network
  partition failure. Metadata disk destruction, correlated power loss and
  restore from off-cluster backup are not covered by the data-disk test.

## Directory structure and sources

```text
garage/s3-cluster/
  Makefile
  docker-compose.yml
  control.py                         # native controller / durable history
  traffic.py                         # independent paced writer and reader
  placement.py                       # read native replica assignment table
  replica_audit.py                   # read-only exact-owner/block-byte audit
  witness.py
  cold_reopen.py
  report.py                          # saved evidence; no live storage required
  test_{config,control,placement,teardown,traffic,reader}.py
  prepare_build.py
  docker/Dockerfile.tools
  audit-requirements.txt
  s3-workload -> ../../object-storage/client
  benchmark.py -> ../../object-storage/benchmark.py
  witness_archive.py -> ../../object-storage/client/run_state.py
  scripts/{config,bootstrap,runtime_config}.py
  config/generated/                  # ignored TOML / bootstrap identity
  .build/tools/                      # ignored regular-file Docker context
  results/                           # ignored raw evidence / archived histories
```

[Production guide](https://garagehq.deuxfleurs.fr/documentation/cookbook/real-world/),
[S3 compatibility](https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/).
Pinned 2.5.0 source paths inspected:
[`src/block/manager.rs`](https://github.com/deuxfleurs-org/garage/blob/v2.5.0/src/block/manager.rs)
(fsync, delayed missing-block checks),
[`src/block/resync_queue.rs`](https://github.com/deuxfleurs-org/garage/blob/v2.5.0/src/block/resync_queue.rs)
(native scheduling),
[`src/block/layout.rs`](https://github.com/deuxfleurs-org/garage/blob/v2.5.0/src/block/layout.rs)
(disk markers),
[`src/util/data.rs`](https://github.com/deuxfleurs-org/garage/blob/v2.5.0/src/util/data.rs)
(BLAKE2b-512 truncated to 32 bytes), and
[`src/api/admin/repair.rs`](https://github.com/deuxfleurs-org/garage/blob/v2.5.0/src/api/admin/repair.rs)
(native repair launch semantics).

The stricter audit also inspected pinned native
[`LayoutVersion` lookup](https://github.com/deuxfleurs-org/garage/blob/v2.5.0/src/rpc/layout/version.rs),
[`G010lh` layout schema](https://github.com/deuxfleurs-org/garage/blob/v2.5.0/src/rpc/layout/mod.rs),
[`MessagePack migration codec`](https://github.com/deuxfleurs-org/garage/blob/v2.5.0/src/util/migrate.rs),
[`metadata synchronization/quorum`](https://github.com/deuxfleurs-org/garage/blob/v2.5.0/src/table/sync.rs)
and [structured node statistics](https://github.com/deuxfleurs-org/garage/blob/v2.5.0/src/api/admin/node.rs),
[native bootstrap configuration](https://github.com/deuxfleurs-org/garage/blob/v2.5.0/src/util/config.rs)
and [full-copy metadata quorum](https://github.com/deuxfleurs-org/garage/blob/v2.5.0/src/table/replication/fullcopy.rs).
The actual 2.5.0 CLI help and admin API schema are saved with the run evidence.
The CLI JSON request encoding uses `null` for unit requests and
`{"node":"*","body":null}` for all-node statistics; the public HTTP schema's
query parameters are not the CLI's complete serialized request body.

## Future work / TODO

Deferred checks for supported configurations; these are **not executed coverage**.
Keep recorded passes, failures and limitations above. Performance work requires
a separately selected depth; this list does not restart paused experiments.

- [ ] Exercise peer partitions and native quorum loss, measuring rejection, stale-read policy and convergence after healing.
- [ ] Change supported ring topology and resource budgets; verify block movement and draining under live traffic.
- [ ] Test metadata-volume loss, bounded resource/device-I/O failures and supported rolling upgrades.
- [ ] Restore independent metadata/data backups and verify full object bodies and versions against preserved witnesses.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
