# Ceph RGW: BlueStore recovery lab

The S3 API/byte suite, native deep scrubs, benchmarks, separate guest-crash and
permanent disk-loss tests, and full cleanup passed. Three independent Linux
guests run native Ceph Tentacle 20.2.5. Nine owned virtual SCSI disks hold
BlueStore data; no host disk, privileged container, or host loop device is used.

## Quick start

1. `make docker-up` — build and boot three isolated Linux guests.
2. `make up` — provision MON, MGR, OSD and RGW services; create the S3 bucket.
3. `make test` — check S3 semantics and every object's external SHA-256.
4. `make down` — delete the bucket/objects; keep all Ceph and RGW infrastructure running.
5. `make docker-down` — remove only this example's guests, disks and images.

Requires an **ARM64 Docker engine**, Docker Compose and Python 3. The pinned
guest/package architecture is ARM64; x86 package mixing is rejected. Allow at
least 11 Docker CPUs and 22 GiB Docker RAM for independent traffic observers.
Recommend 35 GiB free disk; the bounded fixture enforces a 25 GiB pre-boot minimum
and a 10 GiB runtime floor. The follow-up started with 28.59 GiB after image
builds; it is not a full-device capacity test.
QCOW2 disks are sparse and can grow well beyond initial usage.

## Preserved-history production workflow

The 2026-11-15 results below describe the earlier combined read/write harness.
The independent writer/reader follow-up is recorded separately below; do not
interpret the historical numbers as execution evidence for the new paths.

`make test-production` runs the API suite and four separate operations:

- `make failover`: crash guest 0's Linux kernel through QMP, affecting its three
  OSDs, MON, MGR and RGW; recover the retained disks.
- `make disk-failure`: detach the serial-allowlisted SCSI data disk, fence its old
  activation, mark its OSD out, require native backfill and `safe-to-destroy`, then
  create the replacement with a pre-recorded new OSD UUID. The numeric OSD ID stays
  the same. Existing replacement claims are activated instead of destroyed again.
- `make rebuilt-member-failure`: a separate kernel crash after disk replacement;
  require the spare's persistent mapping, new LVM/OSD UUID and old serial's absence.
- `make gateway-failure`: kill the single HAProxy process. The three RGWs do not
  remove this routing SPOF; errors and recovery gaps must remain visible.

Two example-owned Compose containers independently issue PUTs and GETs. PUTs
use unique 32 KiB bodies, one SDK attempt and a fsynced intent/outcome journal
outside the guest disks. Reads alternate historical and current ACKs and check
complete SHA-256, length and application version. Missing acknowledged immutable
objects and wrong bytes fail immediately; transport failures are recorded as
availability observations. No ambiguous PUT is replayed.

Both roles run through a frozen ACK-prefix native repair/deep-scrub barrier and
complete prefix readback. Only then does the writer stop; the reader continues
through the final tail reconciliation. Each later test rechecks all earlier
attempt/ACK journals. Seventeen systemd PID/start/restart-count witnesses and three
kernel boot IDs distinguish an unaffected guest from an unaffected daemon.

Native proof requires every RGW pool at `size=3,min_size=2`, settled PG/PGP
placement, three distinct CRUSH hosts per PG, MON quorum, active/standby MGRs,
all nine OSDs up/in, and fresh completed deep-scrub stamps without checksum errors.
Full external S3 bodies remain a separate oracle. This uses the native BlueStore
scrub implementation, not an independent on-disk decoder or a count of filenames.
The maintained Boto3 S3 harness is an explicit Python-workload exception; controllers
and identity/failure checks use the standard library and native Ceph/systemd/QMP tools.

`FAULT_DURATION=1800` is a safety ceiling. The generators use one outstanding
request and a 50 ms delay; these fault rates are not capacity benchmarks. Each
traffic container has 2 CPUs / 2 GiB and the native control container 1 CPU /
512 MiB, in addition to the store caps below. Fault-phase CPU caps can oversubscribe
the shared 11-CPU Docker VM. Benchmarks run one workload and no fault traffic.

`make up` binds a fresh fixture or verifies the same retained identity; it never
truncates existing witnesses. `make verify`, `make report`, `make cold-reopen`,
`make unit-test` and diagnosed `make recover-bootstrap` are additional commands.
`make recover-replacement RECOVERY_CASE=<failed-disk-case>` uses native recovery
and verifies all histories before rebinding the declared new OSD. Failed cases
retain their original failed status. Whole-stack reopen stops all guest kernels;
it tests persistence, with warm host caches and no availability claim.

If the initial package installation was interrupted before any cluster provisioning,
`make recover-guest-init` completes `dpkg`/the pinned local ISO installation in place.
It refuses once a cluster bootstrap plan exists. The resumed fixture exercised this
path: all three guests had unpacked/unconfigured packages, then passed dependency
checks and the exact 20.2.5 package check without recreating their disks. Original
package logs and separate recovery logs remain available.

Local `s3-workload`, `benchmark.py` and `witness_archive.py` symlinks point to
tracked repository source. `prepare_build.py` materializes regular files into
ignored `.build/tools` before Docker builds. The example does not execute a
sibling Makefile or use its generated state. `results/recovery` preserves the new
witnesses separately from historical `results/verified` measurements.

## Independent traffic follow-up (2026-11-17)

All four separate core fault cases passed with the default 32 KiB
immutable writes. Each row is a separate fault; readback includes all previous
acknowledgements and the API fixture. All 321 current PGs completed fresh native
deep scrubs with three copies on three distinct guest hosts after each recovery.

| Fault | PUT ACK / ambiguous | Successful GET / transport errors | Longest successful-read gap | Complete final readback |
|---|---:|---:|---:|---:|
| Guest 0 kernel crash | 2,905 / 1 | 6,033 / 2 | 21.656 s | 3,975 objects / 202,084,352 bytes |
| One SCSI disk removed; empty spare rebuilt | 2,945 / 0 | 6,312 / 0 | 2.898 s | 6,920 objects / 298,586,112 bytes |
| Rebuilt member kernel crash | 2,977 / 3 | 6,556 / 1 | 24.826 s | 9,899 objects / 396,201,984 bytes |
| Single HAProxy SIGKILL | 1,492 / 500 | 4,408 / 501 | 30.349 s | 11,391 objects / 445,091,840 bytes |

All four cases had **zero missing acknowledged objects, wrong bodies, stale versions
or unknown keys**. The guest crash's one ambiguous PUT was present with the exact
attempted body; lack of an ACK does not imply absence. None of the cases replayed
an ambiguous operation. The disk case retained the numeric OSD ID and changed its
native/LVM UUID; the other eight OSD identities and unaffected daemon processes
remained unchanged. Guest crash moved the active MGR to its surviving standby.

The complete aggregate and offline report passed: **10,319 ACKs / 10,823
attempts**, four retained histories and three exact optional ambiguous outcomes
present. Post-benchmark readback verified **13,583 objects / 855,937,024 bytes**
without data errors. Full-stack cold reopen passed in **347.914 s**: all three
kernel boot IDs changed, both containers and the guest volume were retained, and
cluster/OSD UUIDs and full bodies survived. The stricter offline report passed.
This is shutdown persistence with warm host caches, not an availability or cold-cache
benchmark. Retained-state `make up` and the subsequent report passed without
reseeding. Direct SDK bucket/body probes passed on all three RGWs after reopen,
and all three proxy backends were healthy. The fresh default five-command
lifecycle, final verification/report and both scoped cleanup checks passed.
The fresh fixture verified **1,069 objects / 106,860,544 bytes** and all **321 PGs**,
with zero reconciliation errors. The earlier manifest, attempt/ACK histories,
writer/reader journals and case-status hashes were archived unchanged; the new
fixture and cluster UUIDs differed. All 39 recorded source/configuration hashes,
including linked SDK source, remained unchanged during fresh qualification.
Both cleanups left **zero owned containers, volumes, networks or build tags**;
all ten unrelated volume identities stayed unchanged.
Twenty-seven focused SDK, journal, reader, capacity, native-identity, teardown and
report-freshness controls passed, along with five offline probes of the actual native scrub proof.
These are correctness controls, not extra live fault experiments.

### Fresh default lifecycle timings

Measured once with new guest disks and the final code/configuration. The slow
initial PG scrub/rescheduling dominates `up`; these include controller overhead
and are not database capacity or recovery benchmarks. Extra `verify` (131.325 s)
and `report` checks ran before cleanup.

| Command | Wall time |
|---|---:|
| `make docker-up` | 419.276 s |
| `make up` | 1077.236 s |
| `make test` | 105.326 s |
| `make down` | 11.872 s |
| `make docker-down` | 1.444 s |

### Follow-up warm benchmark

`make benchmark` completed in **108.585 s wall time**, including preparation and
tool startup: **4,384 timed requests, zero failures**, one run on the same M4 Pro
and guest/container caps documented below. The 12 cells use 1 and 16 clients;
1,000 requests per 4 KiB cell, 64 per 1 MiB cell and 32 per 4 MiB cell.
Each GET cell follows its PUT cell with full hashing and warm caches. This is a
bounded software-emulation result, not native Ceph capacity or a cold-query test.
The prior core repair proof covered 321 PGs; autoscaling remains enabled.

| Object | Clients | PUT ops/s | PUT MiB/s | PUT p50/p99 ms | GET ops/s | GET MiB/s | GET p50/p99 ms |
|---|---:|---:|---:|---|---:|---:|---|
| 4 KiB | 1 | 26.11 | 0.10 | 35.91 / 74.19 | 87.48 | 0.34 | 10.87 / 20.20 |
| 4 KiB | 16 | 66.41 | 0.26 | 236.20 / 386.39 | 384.67 | 1.50 | 39.15 / 83.66 |
| 1 MiB | 1 | 8.03 | 8.03 | 113.74 / 237.80 | 20.20 | 20.20 | 47.18 / 123.07 |
| 1 MiB | 16 | 19.11 | 19.11 | 713.18 / 1345.37 | 40.91 | 40.91 | 348.41 / 886.86 |
| 4 MiB | 1 | 2.76 | 11.02 | 354.95 / 524.56 | 8.42 | 33.67 | 101.34 / 200.18 |
| 4 MiB | 16 | 5.57 | 22.28 | 2246.45 / 5277.02 | 12.09 | 48.35 | 1071.64 / 2584.13 |

## Architecture

| Role | Count | Placement |
|---|---:|---|
| MON | 3 | One per guest; quorum requires 2 |
| MGR | 2 | Active/standby on separate guests |
| BlueStore OSD | 9 | Three virtual block devices per guest |
| RGW | 3 | One per guest, one logical RGW zone |
| Object copies | 3 | CRUSH host failure domain, `size=3`, `min_size=2` |

The guest-to-guest Ceph network uses QEMU's isolated socket/multicast LAN.
Separate SLIRP management NICs provide SSH and S3 forwarding. Advertising an
OSD's private address behind three separate NAT networks does not work: Ceph
clients and peers must reach the advertised messenger/heartbeat addresses.

Guest RAM is 5 GiB each; the container cap is 16 GiB and 8 CPUs. Each OSD uses a
**lab-only 1 GiB memory target**, below the usual 4 GiB target. Recovery and RGW
can still need extra memory. Twelve-GiB sparse virtual disks are deliberately
small. These settings are not production sizing guidance.

## Operational gotchas

| Gotcha | Why it matters / recovery boundary |
|---|---|
| MON quorum is separate from OSD availability | Losing two MONs blocks control-plane progress even if data disks survive. Three OSD replicas do not replace MON quorum. |
| CRUSH failure domains must match hardware | Nine OSDs on three hosts are not nine independent host copies. The rule must choose three distinct `host` buckets. In this local lab all guests still share one physical machine. |
| Three OSDs per host are not extra host copies | A lost disk can backfill onto the other two OSDs in that host domain before replacement. A whole lost host leaves only two domains; full three-host redundancy must wait for its return or a fourth host. |
| `min_size` is an I/O gate | With `size=3,min_size=2`, writes can continue on two copies. Lowering `min_size` to hide an outage changes the durability contract. |
| RGW has more than a data pool | Root, metadata, log, bucket index and data pools need deliberate replication and health checks. Healthy payload objects alone do not establish a usable S3 namespace. |
| An RGW zone is not a CRUSH host | Three gateways in one RGW zone serve one object namespace. This does not demonstrate multisite replication or disaster recovery. |
| `down`, `out`, `destroy`, `purge` differ | A temporary guest outage should not trigger immediate destructive replacement. For a permanently lost OSD: mark out, wait for backfill, check `safe-to-destroy`, then replace. Never zap a disk identified only by an unstable `/dev/sdX` name. |
| Fullness limits stop writes and recovery | Spare capacity must exist in the surviving CRUSH failure domains. Replacing a disk at the full ratio is not a guaranteed recovery plan. Sparse QCOW2 apparent size also does not equal host free space. |
| PG autoscaling can invalidate a scrub coverage snapshot | Fresh setup changed PG membership and initially completed only 177/289 new scrub stamps, despite `HEALTH_OK`. The checker restarted coverage and rescheduled unscheduled PGs, including a final `7.77`; all 289 then completed. Sequential native CLI checks are slow under TCG. Health alone did not qualify the new copies. |
| Recovery is not just `ceph -s` returning | Require all expected OSDs up/in, PGs `active+clean`, completed deep scrubs without inconsistent PGs, and full external S3 checksum reconciliation. |
| Health is not a backup | Replication copies deletion and operator mistakes. Backups and multisite RPO are separate concerns. |
| A process kill is not a power cut | Guest crash drops a whole Linux kernel. QMP device removal exercises a disappearing block device. Neither proves physical power-loss protection of the host drive/controller. |
| Small PG/pool and memory choices distort benchmarks | Report the exact pools, PG counts, copies and caps; do not present these as default production capacity. |
| Emulated guest clocks can delay health recovery | A rebuilt-member restart briefly reported MON clock skew while OSDs caught up. The check waited for `HEALTH_OK`; it did not relax the clock-skew or durability thresholds. |
| Emulation distorts throughput | QEMU TCG is software emulation even on ARM64-on-ARM64. Compare recovery semantics with the native S3 examples; do not rank these throughput numbers against native containers. |

## Initial API correctness

The API portion verified **1,069 objects / 106,860,544 bytes**, with zero missing
acknowledged keys, corrupt bytes, stale versions or unknown keys. Defaults are
`OBJECTS=1000` (4 KiB), `LARGE_OBJECTS=64` (1 MiB); cases include 32 MiB multipart,
copy, Unicode, range, overwrite/delete and multipart abort. All eleven shared negative
client controls and direct SDK probes through each of the three RGW endpoints
passed. Native placement, completed deep scrubs and full reconciliation were
checked again after recovery.

## Historical benchmark (2026-11-15, software-emulated guests)

Measured 2026-11-15, Apple M4 Pro, Docker Linux aarch64, 11 CPUs / 24.42 GiB RAM,
Compose 5.1.4. Only this storage SUT was active. Three QEMU TCG guests each have
3 vCPUs / 5 GiB; their host container is capped at 8 CPUs / 16 GiB. The client
has 2 CPUs / 2 GiB; HAProxy 0.5 CPU / 128 MiB. These are **emulation and private
forwarded-network results**, not native Ceph capacity, and must not be ranked
against the native-container S3 tables. Baseline before the benchmark: eight
replicated pools and 257 PGs; autoscaling remains enabled.

`make benchmark`: `BENCH_SMALL_REQUESTS=1000`, `BENCH_REQUESTS=64`,
`BENCH_LARGE_REQUESTS=32`, per operation/concurrency. **4,384 timed requests,
zero request failures** in each of two qualified three-gateway runs. The table
reports the second run, after a clean rebuild of the guests. Unique incompressible
bodies; preparation and the external intent manifest are outside the timer;
SDK signing, checksums, transfer and complete GET hashing are inside. GET immediately
follows PUT and is warm. These are bounded runs; at 32/64 samples p99 is the maximum.

| Object | Clients | PUT ops/s | PUT MiB/s | PUT p50/p99 ms | GET ops/s | GET MiB/s | GET p50/p99 ms |
|---|---:|---:|---:|---|---:|---:|---|
| 4 KiB | 1 | 23.76 | 0.09 | 40.39 / 65.79 | 74.75 | 0.29 | 12.56 / 24.90 |
| 4 KiB | 16 | 58.17 | 0.23 | 263.79 / 483.01 | 333.73 | 1.30 | 44.01 / 99.14 |
| 1 MiB | 1 | 6.85 | 6.85 | 141.45 / 391.23 | 19.28 | 19.28 | 52.91 / 85.73 |
| 1 MiB | 16 | 13.47 | 13.47 | 1073.59 / 1955.23 | 33.73 | 33.73 | 422.81 / 1124.29 |
| 4 MiB | 1 | 1.91 | 7.63 | 498.22 / 720.25 | 5.74 | 22.95 | 181.68 / 261.49 |
| 4 MiB | 16 | 4.32 | 17.26 | 3462.08 / 5215.34 | 10.34 | 41.36 | 1320.55 / 2706.73 |

The latest qualified benchmark/disk results are in ignored `results/verified/`;
the independently qualified guest-crash run is in `results/guest-crash-qualified/`.
The rejected one-gateway run and its intact-ACK audit are retained in `results/rejected-proxy-run/`.

## Failure contract

Separate process/guest-crash and permanent disk-loss cases keep the producer
running, record attempts and acknowledgements outside Ceph, and never blindly
retry an ambiguous PUT. Disk replacement must use the allowlisted `cephlab-*`
serial and an empty owned spare. A pass requires zero lost acknowledged objects,
zero corrupt or stale bodies, native placement/repair checks and an explicit
accounting of requests that failed during the outage.

Run `make failover` for a whole guest crash and `make disk-failure` for permanent
loss of one BlueStore disk. `FAULT_DURATION=1800` bounds the continuous producer;
`OBJECTS`, `LARGE_OBJECTS` and the three `BENCH_*_REQUESTS` Make variables control
workload sizes. Run the fault targets before the final two cleanup steps.

### Measured recovery

2026-11-15, the hardware/caps above. These were independent qualified fixtures;
the disk test also power-cycles the repaired guest while requests continue.
The other two guest boot IDs remained unchanged in both cases.

| Case | Fault hold s | First subsequent ACK s | ACKs during hold | Total attempts / ACKs | Ambiguous PUTs / GET errors | Native clean s | Final verification s |
|---|---:|---:|---:|---|---|---:|---:|
| Whole guest crash | 30.29 | 30.00 | 3 | 1,529 / 1,527 | 2 / 2 | 187.47 | 285.24 |
| Disk unplug + replacement + persistence restart | 30.32 | 0.17 | 229 | 2,746 / 2,745 | 1 / 2 | 382.83 | 580.42 |

Times are measured from injection; native-clean and final-verification times
include the deliberate hold and recovery work. The guest-crash case had nearly
30 seconds before another successful PUT; keeping the producer running does not
promise uninterrupted successful requests. Ambiguous writes are reconciled as
optional outcomes with exact bytes, never retried blindly or counted as ACKs.

Guest crash reconciled **4,789 objects / 567,775,232 bytes**. Disk replacement and
the final `make test` reconciled **6,006 objects / 607,653,888 bytes**. Both had
**0 missing acknowledged keys, 0 corrupt bodies, 0 stale versions, 0 unexpected
keys**. Native audits required nine OSDs up/in, three distinct CRUSH hosts per
PG, all pools size3/min2, and completed deep scrubs of all **257 PGs**.

The disk test unplugs the owned SCSI device, waits for native backfill and
`safe-to-destroy`, reuses the destroyed OSD ID on an empty spare with
`ceph-volume`, and requires full recovery. It then gracefully powers off the
repaired guest and proves a new boot ID, replacement serial present, old serial
absent, and native clean state. The producer runs through this restart too.
This caught both activation-unit handling and persistent virtual-disk mapping
issues that a successful read from a surviving copy would miss.

The final `make down` deleted all **6,006 objects**, aborted **0** outstanding
uploads, and removed the bucket while infrastructure remained running.
`make docker-down` then removed the example's containers, volume, network and
built images. No global Docker cleanup is used.

## Limits

This is a recovery lab using production Ceph daemons and native operational
guards, not a production deployment certification. It uses private HTTP and
fixture credentials, logical host failure domains on one physical computer,
small disks/OSD memory and emulated guests. TLS/identity hardening, independent
host power loss, two-host failure, network partitions, full disks, backup restore,
multisite disaster recovery and the complete AWS S3 feature surface were not
qualified here. Interrupted replacement/bootstrap re-entry helpers have not been
fault-injection qualified in this follow-up; package-init repair was exercised
before provisioning. Separate isolated MON, MGR, RGW and OSD daemon crashes, quorum
loss, scale changes, upgrades/rollback and bounded resource-pressure cases remain
unrun; a whole-guest crash does not qualify those independently. Production
capacity and recovery times require real hardware,
representative occupancy and independent failure domains.

## Sources inspected

- [Tentacle releases](https://docs.ceph.com/en/latest/releases/): maintained
  20.2.5 point release selected over a newly introduced major release.
- [Native ceph-volume manpage](https://docs.ceph.com/en/tentacle/man/8/ceph-volume/),
  [ceph-osd](https://docs.ceph.com/en/tentacle/man/8/ceph-osd/),
  [radosgw-admin](https://docs.ceph.com/en/tentacle/man/8/radosgw-admin/).
- [RGW S3 API](https://docs.ceph.com/en/tentacle/radosgw/s3/).
- [BlueStore configuration](https://docs.ceph.com/en/tentacle/rados/configuration/bluestore-config-ref/),
  [OSD replacement](https://docs.ceph.com/en/tentacle/rados/operations/add-or-rm-osds/),
  [CRUSH maps](https://docs.ceph.com/en/tentacle/rados/operations/crush-map/).
- Pinned source: [`ceph-osd.cc`](https://github.com/ceph/ceph/blob/v20.2.5/src/ceph_osd.cc),
  [`osd.yaml.in`](https://github.com/ceph/ceph/blob/v20.2.5/src/common/options/osd.yaml.in),
  [`vstart.sh`](https://github.com/ceph/ceph/blob/v20.2.5/src/vstart.sh).
  [`OSDMonitor.cc`](https://github.com/ceph/ceph/blob/v20.2.5/src/mon/OSDMonitor.cc)
  enforces PG/PGP and destructive OSD command guards;
  [`ceph-volume prepare`](https://github.com/ceph/ceph/blob/v20.2.5/src/ceph-volume/ceph_volume/util/prepare.py)
  handles destroyed-ID reuse, tmpfs metadata and native activation.
  Developer regular-file OSDs were considered and rejected for this example;
  native `ceph-volume` provisions actual guest block devices instead.

## Failures and configuration pitfalls reproduced in this lab

These observed setup and qualification failures led to fixes before the passing
runs above; failed attempts remain separate from qualified results:

- The ARM64 guest's default allocator stack capture crashed inside
  `libgcc_s::_Unwind_Backtrace` via `libtcmalloc`, even for `ceph-osd --version`.
  The MON initially raised SIGILL on `AUTIA1716`. The QEMU lab CPU disables
  pointer authentication (`max,pauth=off`), and all Ceph CLI/daemon environments
  select `TCMALLOC_STACKTRACE_METHOD=generic_fp`. The OSD version probe then
  succeeded. This is scoped runtime compatibility guidance, not a recommendation
  to disable CPU features on production hosts. Frame-pointer stack capture may
  produce less complete profiling traces when code lacks frame pointers.
  See the [upstream allocator guide](https://github.com/gperftools/gperftools/wiki/gperftools'-stacktrace-capturing-methods-and-their-issues)
  and [implementation](https://github.com/gperftools/gperftools/blob/gperftools-2.15/src/stacktrace.cc).
- Bulk `dpkg -i` of an offline package closure upgraded runtime libraries while
  leaving preinstalled Ubuntu development packages with incompatible versions.
  The final image stages the signed Ceph packages on an ISO but installs them
  with APT dependency resolution and `apt-get check`. **Guest setup needs access
  to Ubuntu package mirrors**; it is not a fully offline bootstrap. Ceph binaries
  are pinned to `20.2.5-1noble`; Ubuntu dependencies follow the distribution.
- The guest cloud image is dated and SHA-256 pinned. Root SSH is public-key-only
  over the private management route. Dynamic SSH MOTD is disabled because it
  caused long first-connection delays under software emulation.
- Native MON creation needs a Ceph-readable keyring and directory ownership.
  A bootstrap keyring under root's sticky `/tmp` also prevented a root overwrite
  after ownership changed; the example uses a dedicated `/etc/ceph` path.
- Msgr2 is explicitly enabled and insecure global-ID reclaim disabled after
  the initial MON quorum. Do not silence health warnings without understanding
  the underlying feature/configuration.

- With autoscaling on, forcing `osd_pool_default_pgp_num=8` broke RGW pool
  creation with `ERANGE` (`pg_num < pgp_num`): new autoscaled pools start at one
  PG. The example keeps PGP at **0 (match PG)** and waits for nine OSDs up/in
  before creating RGW pools. Do not increase monitor PG limits to conceal a
  PG/PGP mismatch. This was reproduced and diagnosed against the actual MON
  configuration. See the [PG configuration reference](https://docs.ceph.com/en/tentacle/rados/configuration/pool-pg-config-ref/).

- Local config files override the MON database; changing a file does not update
  a running daemon's parsed value. `ceph config show mon.ceph0 ...` exposed the
  old PGP value despite the central setting being zero. A native runtime probe
  applied the corrected value; the final cold-start file uses zero from boot.
  Inspect effective configuration rather than assuming a successful `config set`
  changed it. See [configuration precedence](https://docs.ceph.com/en/tentacle/rados/configuration/ceph-conf/).
- `radosgw-admin user create` is not an idempotent setup check: it returns EEXIST
  for an existing user. The bootstrap lists native users, then reads and validates an existing fixture
  identity/key or creates an absent one. A missing `user info` returned EINVAL
  (22), not ENOENT, in the pinned release; it must not be handled as a generic
  retry or silently converted into success. Other command failures propagate.

- PG/PGP autoscaling continues after pool creation. A summary may briefly show
  `active+clean` while the data pool's placement count still trails its target.
  The first native audit caught this transition. Qualification now waits for
  both current/target PG and PGP counts, checks the direct PG map, requests native
  `ceph osd deep-scrub all`, and requires a newer completed deep-scrub stamp on
  every current PG. A successful scheduling command is not completed scrubbing.

- A real unplug caused BlueStore to abort on kernel EIO and exhausted the
  native OSD unit's **3 starts / 30 minutes** limit. Stopping the old service
  did not clear that counter. Before starting an empty replacement, reset the
  failed state of that specific OSD after the disk/safety checks. Without this,
  `ceph-volume lvm create` formatted the new disk but systemd refused activation;
  native rollback then purged the new OSD registration and zapped its new LV.
  Formatting success alone is not a successful replacement. The interrupted
  attempt was rejected; all 5,941 remaining objects / 605,523,968 bytes were
  checked intact before its controlled teardown and clean rerun.
- Native `ceph-volume@...` activation units are one-shot jobs and become
  inactive after success. Check the corresponding `ceph-osd@...` services and
  native up/in counts rather than treating an inactive activation job as a
  failed OSD. Apply the allocator environment to activation helpers as well as
  the long-running daemons. Disable the old UUID's activation unit when replacing
  a disk, so a later boot cannot try to activate a missing old LV.

- HAProxy DNS resolution normally avoids assigning one IP to multiple servers
  in a backend. This lab forwards all RGWs through different ports on the same
  QEMU host container, so it needs `resolve-opts allow-dup-ip`. Without it, the
  proxy put two healthy gateways in DNS maintenance. Each backend gets an HTTP S3 readiness probe; the aggregate proxy remains
  available with at least one healthy gateway. A separate native CSV audit
  requires all three UP and real traffic on each before qualification. An original misrouted benchmark had one 502
  during concurrent 4 MiB PUTs; all 3,260 acknowledged objects / 513,511,424 bytes
  were subsequently checked intact, but that benchmark was rejected. No gateway
  crash or kernel OOM was found; the exact cause of that individual 502 was not
  established. HTTP access logging is enabled for subsequent tests. See the
  [HAProxy 3.2 configuration manual](https://docs.haproxy.org/3.2/configuration.html)
  and [pinned 3.2.8 source](https://www.haproxy.org/download/3.2/src/haproxy-3.2.8.tar.gz).

- A TCP health check on a QEMU forwarded port can succeed before the guest's
  RGW is ready. Use a real HTTP response. This pinned RGW returns HTTP 200 and
  an empty anonymous `ListAllMyBucketsResult` at `/`, not 403; the health check
  verifies both status and body marker. Signed SDK probes separately verify
  access to the private fixture bucket through each gateway.


## Files

```text
ceph/s3-cluster/
  Makefile
  docker-compose.yml
  vm/{Dockerfile,boot.py}    # pinned packages, cloud image and owned virtual disks
  native/{guest,bootstrap,observe,recovery,fault}.py
  control.py                # live traffic, native barriers, histories and teardown
  native_state.py           # cluster/OSD and survivor-process identity checks
  capacity.py
  traffic.py                # separate PUT and GET roles
  witness.py
  prefix_readback.py
  cold_reopen.py
  report.py                 # saved evidence only
  test_{capacity,native_state,reader,traffic,teardown,report}.py
  prepare_build.py
  docker/Dockerfile.tools
  s3-workload -> ../../object-storage/client
  benchmark.py -> ../../object-storage/benchmark.py
  witness_archive.py -> ../../object-storage/client/run_state.py
  config/haproxy.cfg
  .build/tools/             # ignored materialized regular-file Docker context
  results/                  # ignored native logs and external journals
```

Native references: [Tentacle administration manual](https://docs.ceph.com/en/tentacle/man/8/ceph/),
[OSD replacement](https://docs.ceph.com/en/tentacle/rados/operations/add-or-rm-osds/),
[ceph-volume create](https://docs.ceph.com/en/tentacle/ceph-volume/lvm/create/),
[activation and persistent UUIDs](https://docs.ceph.com/en/tentacle/ceph-volume/lvm/activate/)
and [native LVM inventory](https://docs.ceph.com/en/tentacle/ceph-volume/lvm/list/).
Pinned daemon/configuration source was also inspected; native CLI help confirmed
the planned `--osd-fsid` option before disk injection.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
