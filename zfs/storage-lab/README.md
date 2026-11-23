# ZFS storage lab: mirror, RAIDZ2 and snapshot replication

A storage-only lab with three selectable setups. Two actual Linux kernels run
ZFS inside unprivileged QEMU containers; upstream fio generates and verifies data.
There is no database, shared writable database file or host ZFS requirement.

The **2026-11-22 default five-command lifecycle passed**, followed by verified
scoped teardown. Independent readers checked **297 complete 64 MiB bodies**, with
zero read failures/mismatches, while fio remained active through all three native
member replacements. The earlier archive-checker failure and explicit-rate
checkpoint remain separate. This is a one-host functional recovery qualification,
not production readiness or a capacity comparison.

## Quick start

Prerequisites: ARM64 Docker with Compose v2, Make, host Python 3.11+ and `ssh-keygen`.
Ensure the `python3` selected by Make meets that version; macOS Python 3.9 is
insufficient. Allow 8 GiB
Docker RAM and 12 GiB free Docker disk. QEMU uses TCG software emulation;
initial boot and offline package installation take several minutes. Virtual
16 GiB devices are sparse, with capacity allocated from the Docker host as used.
No privileged containers, host devices, host kernel modules or Docker socket.

1. `make docker-up` — build the pinned runtime and boot both guest kernels; wait until healthy.
2. `make up` — install the offline ZFS/fio packages and cap each guest's ARC.
3. `make test` — run mirror, RAIDZ2 and snapshot replication tests serially with the default live-write window.
4. `make down` — export imported example pools, retaining the guests and disks.
5. `make docker-down` — archive and verify retained pools, then delete only owned containers, disks, network, image tag and SSH keys.

`make unit-test` runs standard-library negative tests: incomplete/error fio results,
missing receive tokens, corrupt received copies and failed/incomplete source fencing
must stop before promotion. Pool-creation tests reject unrelated failures and
creation retries when a pool already exists. `make test` runs these before live
storage tests.

Tests reset only this project's pools and dedicated virtual disks. `make test
SETUP=mirror`, `SETUP=raidz2` or `SETUP=replication` selects one setup. Separate
aliases: `make mirror-test`, `make raidz2-test`, `make replication-test`.
`make benchmark` runs after correctness and recreates source pools for each layout;
it preserves prior source state in a verified native send stream before recycling
the pool. A tracked exported pool is first imported with `-N` (no filesystem mounts),
fenced read-only, archived and checked before recreation. `make status` shows pools/datasets;
`make cli` opens an SSH shell in the source guest.

## Directory structure

```text
zfs/
├── README.md
└── storage-lab/
    ├── README.md
    ├── Makefile
    ├── docker-compose.yml
    ├── run.py                # host ownership/lock and cgroup resource collection
    ├── resources.py          # native CPU/memory/disk/byte/IOPS summaries
    ├── app/lab.py            # standard-library orchestration; invokes ZFS and fio
    ├── app/observe.py        # independent guest reader and timed native actions
    ├── app/evidence.py       # fsynced histories and native archive gates
    ├── vm/Dockerfile         # pinned Linux image and offline packages; no DuckDB
    ├── vm/boot.py            # guest kernel and six owned sparse disks per guest
    ├── state/               # ignored SSH keys; VM disks live in named volumes
    └── results/             # ignored pool states, hashes and raw fio JSON
```

## Setups and limits

| Setup | Layout and purpose | Failure boundary |
|---|---|---|
| Mirror | Two active devices plus a replacement; fault one member during writes, replace, wait for resilver, verify and scrub | One member can fail. Copies still share a physical host/disk; no corruption injection or host-loss proof. Nominal usable capacity is one device, less overhead. |
| RAIDZ2 | Four active devices plus two replacements; fault two members together under writes, replace both sequentially, verify and scrub | Two-member redundancy at the cost of parity CPU/I/O and capacity. This four-wide layout is a demonstration, not a universal sizing recommendation. No third-failure or RAIDZ expansion test. |
| Replication | Mirrored source and target pools; full send, incremental interruption/resume, read-only clone, export source pool, promote target | Asynchronous snapshot RPO. Acknowledged source files after the last snapshot are absent on the target. Export is administrative fencing, not remote power fencing or automatic HA. An operator could reimport the source; this is not quorum-enforced fencing. |

All devices are virtio disks backed by files on one Docker host. Each guest has
six data devices, using only those needed for its selected layout. There are
192 GiB of nominal data-device addresses at defaults (12 × 16 GiB), not 192 GiB
of reserved physical storage. OS disks are 12 GiB each. No host disk is passed
through. Mirrors are not backups; snapshots on the same pool share its failure
fate. Native RAIDZ expansion, deduplication, SLOG/L2ARC and special vdevs are outside
this lab. Database consistency under active WAL writes is covered separately in
[DuckDB on ZFS](../../duckdb/zfs-recovery/README.md).

| Variable | Default | Meaning |
|---|---:|---|
| `DISK_GIB` | 16 | Each owned device; 8–64, requires clean lifecycle when changed |
| `DATA_MIB` | 512 | Checksummed payload per test; 256–2048 |
| `WRITE_MIB_S` | 4 | Deliberate fault-test rate cap; allow at least 60 s of work |
| `BENCH_MIB` | 256 | Benchmark file per layout; 64–1024 |
| `BENCH_SECONDS` | 10 | Random mixed I/O duration per layout; 5–60 |
| `RESULTS_DIR` | `results/manual` | Raw reports |

The **4 MiB/s** default passed the complete lifecycle below without a
`WRITE_MIB_S` override. It gives finite writers enough work to remain active
through both RAIDZ2 replacements; it does not improve a capacity number. The
[earlier override checkpoint](config/OBSERVER_RESULTS_4M_OVERRIDE.json) preserves the
previously measured 4 MiB/s run and its tested runtime at commit `0cd5c58`, byte
for byte. Historical **8 MiB/s** measurements retain their separate method below.

## Correctness protocol

Fault tests use one fio sync worker, 128 KiB I/O, CRC32C verification headers,
fixed seed, incompressible/refilled buffers, fsync every 8 writes (1 MiB), and a
final fsync. File preallocation is disabled so growth can prove continued writes
while the pool is degraded. A member fault is explicit `zpool offline -f`, not a
simulated physical cable pull. `zpool replace -w` waits for replacement completion;
then a separate full-file fio verification checks every block and offset. A scrub
must finish with an ONLINE pool and no known data errors. Pool status, whole-file
SHA-256 and complete fio JSON are retained. Rate-capped fault-test I/O is not a
throughput benchmark.

Replication uses completed, fsynced files. The target remains unmounted/read-only
during receive. The incremental stream is interrupted after 65,536 bytes; a real
resume token is required, then the resulting clone must match source sizes and
SHA-256 hashes. A later 64 MiB source file is deliberately not replicated. The
source pool is exported before target promotion; the target must lack that tail
and retain all replicated bytes. It must then write and verify another 64 MiB.
This is filesystem recovery of completed files, not a database transaction test.

## Follow-up recovery observations

The existing Python controller is retained to preserve its native fio/ZFS paths;
rewriting it only to change languages would obscure those recovery boundaries.
The new reader also uses Python's standard library and runs as an independent
guest process, rather than blocking behind the fio writer or fault controller.

Mirror/RAIDZ2 cases first write and fsync a separate **64 MiB** immutable file,
record its byte count/SHA-256 outside both guests, then repeatedly read and hash
the complete body. A separate fio writer remains active through member fault and
replacement. UUID case directories preserve prior read histories, failures,
immutable acknowledgements and native-action timing. At least one complete
successful immutable read must occur inside **each actual native replacement
interval**, compared on the same guest monotonic clock. Reads continue through
full payload verification and scrub, with zero wrong bodies required. The existing
fio file-growth signal is still not a count of independently journaled per-flush
application acknowledgements. This extension does not add continuous replication
observers or power-loss guarantees.

Before an independent case recycles a pool, or teardown deletes its devices,
active fio/readers are rejected, datasets are fenced read-only and a recursive
native `zfs send` snapshot stream is fsynced outside the guests. Re-reading that
stream through `zstream dump` checks native checksums, header-aware substream/
compound-package conclusion counts and complete byte count; a SHA-256 manifest is retained. OpenZFS 2.2's dump path can
print a checksum error yet exit zero, so checksum diagnostics on either output
stream also reject the archive. A failed export preserves the original pool. These are **archive
preservation checks, not a demonstrated restore**. Raw archives and histories
remain ignored under `results/`; source, configuration and concise measured
summaries belong in Git.

Each lifecycle operation records native container cgroup CPU core-seconds and
utilization, sampled/lifetime-peak charged memory, owned QCOW2 apparent/allocated
bytes and per-device block read/write bytes and IOPS. Native ZFS used/logicalused/
compression ratios and per-read latency/reader CPU/RSS are separate layers.
Absent I/O devices are unavailable, not zero. Physical SSD latency/queue depth
and engine heap are unavailable. Samples are about 1 s plus sequential probe costs;
all timestamps and gaps are retained. Observers are charged to their containers.
TCG emulation, warm cache and concurrent independent functional labs prevent
these diagnostics from being capacity or comparative benchmark claims.
Provisioning sets the live ARC cap; read-only observations confirm both current
kernels use **268,435,456 bytes** for `zfs_arc_max` and native `c_max`. This member-
fault matrix does not reboot guests. ARC-cap persistence across reboot remains
unqualified; repeat provisioning after a retained boot before relying on its cap.

`make test-production` runs all three applicable cases serially. Resource/lifecycle
and archive/reader negative controls run in `make unit-test`. Supported scaling,
pressure and additional loss combinations remain unrun; there is no Kubernetes
operator in this filesystem-only example.

### Failure symptoms and recovery mechanism

| Case | Detection | Native recovery in the owned source guest |
|---|---|---|
| Mirror member fault | `zpool status -P zfslab` reports DEGRADED and d0 FAULTED, while independent reads and the fio writer continue | `zpool replace -w zfslab /dev/disk/by-id/virtio-zfslab-d0 /dev/disk/by-id/virtio-zfslab-d2`; require complete payload verification, a completed `zpool scrub -w zfslab` and ONLINE/no known data errors. |
| Two RAIDZ2 member faults | DEGRADED with d0/d1 FAULTED; two failed leaves are this layout's declared boundary | Replace d0→d4 and d1→d5 serially with `zpool replace -w`; require successful complete immutable reads inside each replacement interval and continued writer activity until native rebuild finishes, then verify and scrub. |

These are native persistent fault-state injections, not destroyed physical drives.
The replacement devices are separate owned virtual disks; restored membership and
scrub evidence remain in each case. Do not substitute `zpool clear` for rebuilding
lost storage or infer independent-host durability from these one-host copies.

The first follow-up target stopped **before RAIDZ2 reset** because its archive
checker incorrectly assumed every native stream has equal BEGIN/END counts.
The retained valid recursive stream contains **3 BEGIN / 4 END** records and
**605,513,376 bytes**: a compound package has an additional conclusion END in
[OpenZFS 2.2.2's sender](https://github.com/openzfs/zfs/blob/zfs-2.2.2/lib/libzfs/libzfs_sendrecv.c#L2356).
The source pool remained fenced read-only and intact. The exact old stream was
rechecked, with clean native checksums and both expected dataset snapshots; its
original failed verdict remains under `results/continuous-20261122/`. The corrected
gate checks header types and compound conclusions, byte count, dataset coverage,
exit status and diagnostics on both output streams. Its selected actual native
output is a regression fixture. The separate fresh full target subsequently passed;
revalidation does not relabel the failed attempt as a pass.

## Measured default profile: 2026-11-22

The five commands ran separately in `results/continuous-default-20261122/`.
Only `RESULTS_DIR` changed; `DATA_MIB=512`, `WRITE_MIB_S=4`, `DISK_GIB=16` and
`SETUP=all` came from defaults. Boot took **324.352 s**, provisioning **395.210 s**,
`make test` **1,008.198 s**, `make down` **6.022 s** and guarded `make docker-down`
**287.763 s**. The instrumented native full target itself took **1,006.455 s**.
These timings include setup/archival/control work and cannot establish a speedup
against earlier retained-state runs.

Apple M4 Pro, 14 host cores / 48 GiB RAM; Docker VM 11 CPUs / 24.42 GiB,
ARM64 Linux. Two guest containers each had **2 CPUs / 3 GiB**, each guest
**2 vCPUs / 1,536 MiB** and live ARC **256 MiB**; runner **1 CPU / 256 MiB**.
Linux **6.8.0-147-generic**, ZFS userspace/module **2.2.2-0ubuntu9.5**, fio **3.36**.
A separate local DuckDB functional lane and temporary **1 CPU / 2 GiB** Go SDK
build/control containers shared the host under explicit budgets. The SDK jobs
ran serially while DuckDB's standby was stopped; their exact intervals are in
the JSON co-tenancy addendum. No comparative benchmark ran. Runtime source hashes
match the tested files.

Each layout wrote and CRC-verified **512 MiB** in **4,096 × 128 KiB** operations,
plus a separately fsynced **64 MiB** immutable body. The deliberate 4 MiB/s cap
provides a long fault window. Full-body read latency includes SHA-256, excluding
SSH reporting and external journal fsync. This repeated working set fits ARC;
these observations do not establish uncached device-read performance.

| Layout | Faulted leaves | Complete immutable reads / errors | Further degraded write growth | Replace/wait incl. SSH | Scrub incl. SSH |
|---|---:|---:|---:|---:|---:|
| Mirror | 1 | 157 / 0 | 10,878,976 B | 23.376 s | 14.388 s |
| RAIDZ2 | 2 | 140 / 0 | 12,189,696 B | 62.611 s | 20.568 s |

Actual native replacements took **16.541 s** for mirror and **18.505 / 28.210 s**
for RAIDZ2. Each contains complete successful independent reads; the writer
remained active until every replacement completed. Both full payload verifications
and final ONLINE scrubs passed with **0 errors**. Readers continued through scrub.

| Layout / actual phase | Samples | Complete 64 MiB read+hash p50 / p99 |
|---|---:|---:|
| Mirror baseline | 13 | 992.350 / 2,571.741 ms |
| Mirror degraded | 20 | 1,011.759 / 1,956.775 ms |
| RAIDZ2 baseline | 13 | 992.859 / 2,257.217 ms |
| RAIDZ2 degraded | 20 | 1,044.850 / 1,960.418 ms |

Canonical phases use the same guest's actual monotonic command boundaries;
transition-spanning reads are separate. No complete mirror read fits wholly
inside its brief offline command, so that interval has no latency sample, not
zero latency. These small-sample p99s are effectively maxima. All phase counts,
means, percentiles and error counts remain in JSON.

| Layout | Buffered 128 KiB write p50 / p99 | Fsync p50 / p99 |
|---|---:|---:|
| Mirror | 1.122 / 16.056 ms | 28.180 / 74.973 ms |
| RAIDZ2 | 1.090 / 18.743 ms | 43.254 / 145.752 ms |

These are native fio completion timings across each whole fault workload, not
per-phase application commits. Each job issued **511** measured fsyncs plus final
flush; buffered write completion alone is not per-write durable acknowledgement.
The configured write rate is about **32 operations/s**, deliberately capped.

Snapshot replication passed: the **512 MiB** baseline and **64 MiB** delta matched
the received clone's complete SHA-256 bodies. Interruption after **65,536 B**
produced a real resume token. Full/resumed streams were **538,194,928 B / 69.477 s**
and **67,274,248 B / 14.367 s**. The intentional **64 MiB** unreplicated tail was
absent after promotion, with **0 missing replicated bytes**; another **64 MiB** was
written and CRC-verified on the promoted owner. Promotion plus verification/new
write took **30.074 s**. Positive native global listings afterward proved source
pool absent and target imported. This is administrative export/fencing and
asynchronous RPO, not power fencing, quorum ownership or destruction of the old tail.

Whole native-target resources, including setup/archival/control overhead:

| Role | CPU core-s / mean utilization¹ | Sampled mean / max charged memory | Native lifetime memory peak² |
|---|---:|---:|---:|
| Source QEMU/guest | 1,585.116 / 157.55% | 2,412.99 / 2,574.67 MiB | 2,580.86 MiB |
| Target QEMU/guest | 802.902 / 79.80% | 2,002.28 / 2,341.96 MiB | 2,341.96 MiB |
| Runner | 30.784 / 3.06% | 93.96 / 256.00 MiB | 256.12 MiB |

¹ One core = 100%; each guest container permits 200%. Unsampled edges and reset
intervals are excluded. ² Lifetime since container creation, including boot/up,
not a phase peak; summing role peaks is not simultaneous whole-stack memory.
Host-controller CPU/RSS and CLI-child CPU are separate in JSON. These are whole
QEMU/cgroup costs, not ZFS-only CPU or engine heap. Native limits show no VM swap;
the runner allows 256 MiB swap and had zero swap/events at the initial main-run
observation. Later removed one-shot swap/OOM-event counters were not collected.

| Role / VM device | Read / write MiB | Read / write IOPS |
|---|---:|---:|
| Source / 254:0 | 7,105.127 / 4,756.679 | 99.596 / 113.289 |
| Target / 254:0 | 1,155.021 / 1,421.766 | 10.372 / 22.765 |
| Runner | unavailable | unavailable |

Sampling was about 1 s plus sequential probes, with native intervals/counters
retained. These are Docker VM block counters, not physical SSD traffic; do not
sum stacked devices or infer physical latency. Native fio buffered-I/O/fsync
latencies are separate. Per-vdev queue/timing counters were not collected.
Owned QCOW2 allocated bytes (baseline / sampled peak / last sample) were
**215,969,792 / 3,731,501,056 / 3,731,501,056** for source and
**215,445,504 / 1,589,334,016 / 1,589,334,016** for target, including OS/data disks
and recycled space, excluding immutable image layers. Native ZFS used/logical-used/
compression are separate; both fault setups reported **1.00×** dataset compression.
Native network byte/packet observations cover container lifetime through post-test
inspection, not per-phase rates or later teardown traffic.

Four native archives (**2,556,621,936 B**) were preserved and independently
re-hashed after their native checksum/framing/dataset checks. Fresh healthy Docker
listings prove **0 owned containers, volumes, networks and image tags** after
cleanup. Generated SSH keys were removed. All **35 controls** passed: **16 host**
and **19 in the built image**.

The first `make test` attempt failed before native tests because the nested Make
selected Python **3.9.6**, below the documented prerequisite; its child lacked
`hashlib.file_digest`. Its failed log/command and diagnostic remain preserved.
Putting the existing Python **3.14.7** first in the test executor's PATH satisfied
the prerequisite; the same exact `make test` then passed without workload/source
changes. This does not relabel the failed prerequisite attempt as a pass.

[OBSERVER_RESULTS.json](reports/OBSERVER_RESULTS.json) contains independently replayed
cases, phases, resource/storage measurements, runtime/image hashes, archive and
cleanup proofs plus prior-failure references. The earlier explicit 4 MiB/s run
(**317** correct bodies) remains byte-identical in
[OBSERVER_RESULTS_4M_OVERRIDE.json](config/OBSERVER_RESULTS_4M_OVERRIDE.json). The original
checker-failed run remains failed under `results/continuous-20261122/`; the earlier
passed raw cohort is `results/continuous-fixed-20261122/`.

Remaining: fail-fast host-version/child-error diagnostics, reboot-persistent ARC
qualification, independent restore of the recursive archives, uncached degraded
reads, rebuilt-copy isolation, real EIO/disk-full/host loss, beyond-tolerance
failures and repeated isolated performance comparisons. Native replication restore
does not qualify every one of these different boundaries.

## Benchmark method

Run only after all correctness tests pass. Upstream fio 3.36 measures one sync
worker/iodepth 1, sequential 128 KiB write/read and a 4 KiB random 70%-read mix.
Sequential writes include a final fsync; random writes are buffered until the
final fsync, so operation latency is not per-write durable-commit latency.
`direct=0` is explicit: warm ARC/OS caches and TCG dominate these small files.
Numbers must not be presented as physical SSD throughput/IOPS or multi-host HA.
The same file is read after writing; the read test is intentionally warm.

Both containers: 2 CPUs / 3 GiB, no swap above that cap. Guest: 2 vCPUs / 1536 MiB;
ARC 256 MiB; controller 1 CPU / 256 MiB. Pools use `ashift=12`, `compression=lz4`,
`recordsize=128K`, `atime=off`, `sync=standard`. Compression remains enabled but
fio buffers are not zero-filled. No unsafe sync setting is used.

## Known setup issue

Pool recreation under TCG intermittently returned
`one or more devices is currently unavailable` as partition devices were recreated.
After that exact failure, the lab checks that no pool exists, waits for udev and
requires all owned `-part1` devices before one creation attempt using those existing
partitions. Other failures and already-created pools stop immediately. OpenZFS
2.2's Linux labeling path waits for raw `/dev/vdX1`; persistent-link readiness is
handled explicitly here. Successful benchmark pools also receive a separate
64 MiB CRC32C write/verify after measurement. This workaround does not retry
application writes or hide failed storage I/O.

## Measured results

2026-11-14–15, Apple M4 Pro, Docker VM aarch64 (11 CPUs / 24.42 GiB), shared host.
Guest/container/ARC limits are listed above. Two Ubuntu Linux guests use TCG
software emulation; kernel 6.8.0-147-generic, ZFS 2.2.2-0ubuntu9.5 and fio 3.36.
Ten negative unit tests passed; live storage results follow. Fault tests
are deliberately rate-capped at 8 MiB/s; their timings are recovery observations,
not maximum throughput. Raw JSON: `results/verified/`.

| Correctness setup | Faulted members | Verified bytes | Further file growth while degraded | Replace/wait incl. SSH (s) | Scrub incl. SSH (s) | Data errors |
|---|---:|---:|---:|---:|---:|---:|
| mirror | 1 | 536,870,912 | 19,267,584 bytes | 12.028 | 6.487 | 0 |
| RAIDZ2 | 2 | 536,870,912 | 22,020,096 bytes | 29.768 | 9.736 | 0 |

Both pools returned ONLINE. fio verified all 128 KiB CRC32C blocks and offsets
across the full 512 MiB file after replacement, then each scrub finished with no
known data errors. File growth is a continued-write check, not a count of
application acknowledgements. SHA-256 hashes are saved alongside fio output.

| Snapshot replication | Observed |
|---|---:|
| Baseline payload | 536,870,912 bytes |
| Incremental file | 67,108,864 bytes |
| Replicated bytes missing / hash mismatches | 0 / 0 |
| Interrupted receive prefix | 65,536 bytes |
| Full send | 538,194,928 bytes / 56.355 s |
| Resumed incremental send | 67,277,856 bytes / 11.101 s |
| Deliberately unreplicated tail absent on target | 67,108,864 bytes |
| New promoted data written and verified | 67,108,864 bytes |
| Promotion, full verification and new write incl. SSH | 24.560 s |

Source pool export was confirmed before making the target writable. This is
administrative fencing; no primary VM was powered off in this setup. The received
read-only clone matched source sizes and SHA-256 hashes before promotion.

## Measured fio performance

Measured after all three correctness setups passed, with fresh pools and a
256 MiB file per layout. One worker, sync engine, iodepth 1, buffered I/O;
sequential write/read use 128 KiB blocks. Random 4 KiB, 70%-read workload runs
10 seconds. Bandwidth/IOPS and completion percentiles use fio's own accounting.
`end_fsync=1` flushes dirty data at the end; individual buffered write latency
is not durable commit latency. Small, warm files and software emulation do not
measure physical-disk capacity, SSD IOPS or a production deployment.

| Layout | Sequential write (MiB/s) | Warm read (MiB/s) | Random read (IOPS) | Random write (IOPS) | Read p50 / p99 (us) | Write p50 / p99 (us) |
|---|---:|---:|---:|---:|---:|---:|
| mirror | 71.749 | 263.918 | 785.7 | 335.6 | 337.920 / 4145.152 | 692.224 / 5668.864 |
| raidz2 | 38.847 | 254.220 | 519.7 | 223.5 | 374.784 / 10551.296 | 741.376 / 13041.664 |

Raw job output, error codes and pool status: `benchmark-mirror.json` and
`benchmark-raidz2.json` under `results/verified/`. These are one-run observations.

## Sources

Pinned Ubuntu 24.04 ARM64 cloud image 20261017 is SHA-256 checked. It includes
kernel 6.8.0-147-generic and the matching ZFS module; offline userspace packages
are 2.2.2-0ubuntu9.5. fio package: 3.36-1build2. Actual versions are written to
`versions.json`. No host ports are published; SSH is private-network port 2222.

- [ZFS 2.2 vdev layouts and fault limits](https://openzfs.github.io/openzfs-docs/man/v2.2/7/zpoolconcepts.7.html).
- [Faulting a member](https://openzfs.github.io/openzfs-docs/man/v2.2/8/zpool-offline.8.html), [replacement completion](https://openzfs.github.io/openzfs-docs/man/v2.2/8/zpool-replace.8.html), [scrub](https://openzfs.github.io/openzfs-docs/man/v2.2/8/zpool-scrub.8.html).
- [Unmounted pool import](https://openzfs.github.io/openzfs-docs/man/v2.2/8/zpool-import.8.html).
- [Native stream inspection](https://openzfs.github.io/openzfs-docs/man/v2.2/8/zstream.8.html).
- [Snapshot send](https://openzfs.github.io/openzfs-docs/man/v2.2/8/zfs-send.8.html) and [resumable receive](https://openzfs.github.io/openzfs-docs/man/v2.2/8/zfs-receive.8.html).
- [fio 3.36 manual/source](https://github.com/axboe/fio/blob/fio-3.36/HOWTO.rst).

Inspected source: OpenZFS zfs-2.2.2 `zpool_main.c` (fault/replace/scrub waits),
`zfs_main.c` (resumable streams), `libzfs_pool_os.c` (label/partition waits), `module/zfs/vdev_raidz.c` (parity and failure
limits), and `cmd/zstream/zstream_dump.c` (checksum diagnostics, record counts and
zero-exit boundary), `lib/libzfs/libzfs_sendrecv.c` (compound package conclusion); fio fio-3.36 `verify.c` (CRC32C/header/offset checks), `filesetup.c` (allocation), and `engines/sync.c`
(sync read/write). Orchestration and administrative fencing are lab code.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`reports/`](reports/): compact tracked result summaries.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
