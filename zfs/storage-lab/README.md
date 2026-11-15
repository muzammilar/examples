# ZFS storage lab: mirror, RAIDZ2 and snapshot replication

A storage-only lab with three selectable setups. Two actual Linux kernels run
ZFS inside unprivileged QEMU containers; upstream fio generates and verifies data.
There is no database, shared writable database file or host ZFS requirement.

All three live correctness setups and ten negative tests passed. Performance
was measured afterward; results and limitations are below.

## Quick start

Prerequisites: ARM64 Docker with Compose v2, Make and `ssh-keygen`. Allow 8 GiB
Docker RAM and 12 GiB free Docker disk. QEMU uses TCG software emulation;
initial boot and offline package installation take several minutes. Virtual
16 GiB devices are sparse, with capacity allocated from the Docker host as used.
No privileged containers, host devices, host kernel modules or Docker socket.

1. `make docker-up` — build the pinned runtime and boot both guest kernels; wait until healthy.
2. `make up` — install the offline ZFS/fio packages and cap each guest's ARC.
3. `make test` — run mirror, RAIDZ2 and snapshot replication tests serially.
4. `make down` — export imported example pools, retaining the guests and disks.
5. `make docker-down` — delete the example's containers, disks, network, image tag and SSH keys.

`make unit-test` runs standard-library negative tests: incomplete/error fio results,
missing receive tokens, corrupt received copies and failed/incomplete source fencing
must stop before promotion. Pool-creation tests reject unrelated failures and
creation retries when a pool already exists. `make test` runs these before live
storage tests.

Tests reset only this project's pools and dedicated virtual disks. `make test
SETUP=mirror`, `SETUP=raidz2` or `SETUP=replication` selects one setup. Separate
aliases: `make mirror-test`, `make raidz2-test`, `make replication-test`.
`make benchmark` runs after correctness and recreates source pools for each layout;
it intentionally discards prior source state. A tracked exported source is first
imported with `-N` (no filesystem mounts), then destroyed before recreation. `make status` shows pools/datasets;
`make cli` opens an SSH shell in the source guest.

## Directory structure

```text
zfs/
├── README.md
└── storage-lab/
    ├── README.md
    ├── Makefile
    ├── docker-compose.yml
    ├── app/lab.py            # standard-library orchestration; invokes ZFS and fio
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
| `WRITE_MIB_S` | 8 | Deliberate fault-test rate cap; allow at least 60 s of work |
| `BENCH_MIB` | 256 | Benchmark file per layout; 64–1024 |
| `BENCH_SECONDS` | 10 | Random mixed I/O duration per layout; 5–60 |
| `RESULTS_DIR` | `results/manual` | Raw reports |

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
- [Snapshot send](https://openzfs.github.io/openzfs-docs/man/v2.2/8/zfs-send.8.html) and [resumable receive](https://openzfs.github.io/openzfs-docs/man/v2.2/8/zfs-receive.8.html).
- [fio 3.36 manual/source](https://github.com/axboe/fio/blob/fio-3.36/HOWTO.rst).

Inspected source: OpenZFS zfs-2.2.2 `zpool_main.c` (fault/replace/scrub waits),
`zfs_main.c` (resumable streams), `libzfs_pool_os.c` (label/partition waits), `module/zfs/vdev_raidz.c` (parity and failure
limits); fio fio-3.36 `verify.c` (CRC32C/header/offset checks), `filesetup.c` (allocation), and `engines/sync.c`
(sync read/write). Orchestration and administrative fencing are lab code.
