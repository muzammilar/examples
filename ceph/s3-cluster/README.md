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
least 20 GiB Docker RAM and 35 GiB free host/Docker disk for this example alone.
QCOW2 disks are sparse and can grow well beyond initial usage.

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
| Recovery is not just `ceph -s` returning | Require all expected OSDs up/in, PGs `active+clean`, completed deep scrubs without inconsistent PGs, and full external S3 checksum reconciliation. |
| Health is not a backup | Replication copies deletion and operator mistakes. Backups and multisite RPO are separate concerns. |
| A process kill is not a power cut | Guest crash drops a whole Linux kernel. QMP device removal exercises a disappearing block device. Neither proves physical power-loss protection of the host drive/controller. |
| Small PG/pool and memory choices distort benchmarks | Report the exact pools, PG counts, copies and caps; do not present these as default production capacity. |
| Emulation distorts throughput | QEMU TCG is software emulation even on ARM64-on-ARM64. Compare recovery semantics with the native S3 examples; do not rank these throughput numbers against native containers. |

## Initial API correctness

The API portion verified **1,069 objects / 106,860,544 bytes**, with zero missing
acknowledged keys, corrupt bytes, stale versions or unknown keys. Defaults are
`OBJECTS=1000` (4 KiB), `LARGE_OBJECTS=64` (1 MiB); cases include 32 MiB multipart,
copy, Unicode, range, overwrite/delete and multipart abort. All eleven shared negative
client controls and direct SDK probes through each of the three RGW endpoints
passed. Native placement, completed deep scrubs and full reconciliation were
checked again after recovery.

## Benchmark (software-emulated guests)

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
qualified here. Production capacity and recovery times require real hardware,
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
  app/{control,bootstrap,recovery}.py
  scripts/fault.py
  config/haproxy.cfg
  results/                  # ignored native logs and external journals
```
