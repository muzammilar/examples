# DuckDB on replicated ZFS: clients, snapshots and failover

Multiple DuckDB client processes write through one Quack server. Its native
DuckDB database and WAL live together on ZFS. A second Linux guest receives
snapshots, exposes read-only clones and becomes the owner after a fenced-primary
power cut. This tests asynchronous disaster recovery, not multi-master writes.

The recovery workload and complete five-command lifecycle passed from fresh
guests. Example-owned containers, disks, network and image tag were removed. DuckLake is a separate backlog item. For filesystem-only mirror, RAIDZ2 and
replication experiments, see the [dedicated ZFS lab](../../zfs/storage-lab/README.md).

## Quick start

Prerequisites: ARM64 Docker with Compose v2, Make and `ssh-keygen`. Allow 8 GiB
Docker RAM and 12 GiB free Docker disk. Virtual disks are sparse; do not fill the host filesystem. This example boots two actual Linux
kernels with QEMU software emulation inside containers without `--privileged`. The first
image download and guest provisioning take several minutes. No host ZFS module,
privileged container, host device or Docker socket is required.

1. `make docker-up` — build the VM/client image; boot two Linux guests and wait for ZFS readiness.
2. `make up` — install pinned DuckDB artifacts, create the guests' dedicated pools and start the primary owner.
3. `make test` — run disk replacement, retained-disk VM crash, then asynchronous standby recovery.
4. `make down` — stop the reachable database owner; keep the guest infrastructure and disks.
5. `make docker-down` — delete this project's guests, virtual disks, network, image tag and generated SSH key.

Test state is isolated to example-owned virtual disks. The test resets its own
ZFS datasets. A successful failover deliberately leaves the old primary powered
off and the standby serving writes. To repeat, use the complete clean lifecycle.
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
│   └── lab.py               # independent clients, ACK journal, exact recovery oracle
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
| `make crash-test` | Cut the whole primary guest while writers and WAL are active. Restart that guest with the same disks, recover the WAL, require zero missing acknowledgements, then commit ten new batches. |
| `make replica-test` | Snapshot/send/resume tests below, then fence the old owner and promote the asynchronous standby. Require zero loss from replicated phases; report the intentionally unreplicated tail separately. |
| `make test` | Run those three tests serially; the replica test leaves the standby active. |
| `make benchmark` | After successful `make test`, append `BENCH_ROWS` with no deliberate delay and measure warm full/customer queries. |

`disk-test` consumes its replacement disk; repeating the complete suite needs a
fresh lifecycle. Mirror faulting is controlled ZFS fault injection, not physical
hot-unplug or silent-corruption injection. The VM-crash recovery timer includes
VM restart and health waits; replica-promotion timing does not restart a guest.

## Limitations

| Component/scenario | Boundary |
|---|---|
| Mirrored pool | Survives one injected member fault; both virtual disks still share one physical Docker host/disk. A mirror is not a backup. Capacity is sparse and not reserved. |
| Retained-disk crash | Tests abrupt guest termination and WAL recovery, not loss of both mirror members, a failed storage controller or host power-loss guarantees. |
| Asynchronous replica | Snapshot lag can leave acknowledged rows absent after promotion. No zero-RPO guarantee, automatic fencing, failback or divergent-copy merge. |
| DuckDB clients | One Quack server owns the writable file; four processes are remote clients, not four independent file writers. Quack is beta in this pinned release. |
| Read-only clones | Recover/checkpoint a writable clone before publishing it read-only; do not query a dataset during receive. |
| Measurements | QEMU TCG emulation, synthetic rows and one shared host. Timings are not native ZFS/SSD or production DuckDB performance. |

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

## Benchmark

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
are baked into the Docker image and installed offline in the guests. `versions.json` records the actual
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
