# Garage: replicated S3 and permanent disk loss

Nine Garage 2.5.0 storage nodes distribute three copies across three logical
zones. Separate metadata/data volumes make permanent data-disk replacement
observable. SQLite metadata, consistent quorum mode and data fsync are explicit.

## Quick start

1. `make docker-up` — start nine nodes and the S3 load balancer.
2. `make up` — build the client, configure placement/credentials and seed data.
3. `make test` — verify S3 semantics and complete object bodies.
4. `make down` — delete the example bucket and its objects; keep the cluster running.
5. `make docker-down` — destroy its network/volumes and remove its client image.

Requires Docker Compose, Python 3 and approximately 10 GiB available Docker disk
space. S3 is internal at `s3:9000`; the test credentials are local fixtures, not
production secrets. No host ports or host devices are needed.

## Setup

| Component | Count | Limits / behavior |
|---|---:|---|
| Garage `dxflrs/garage:v2.5.0` | 9 | 1 CPU, 768 MiB each; three nodes per logical zone |
| HAProxy `3.2.8-alpine` | 1 | 0.5 CPU, 128 MiB; nine S3 frontends |
| Docker-built Boto3 client | 1 | 2 CPUs, 2 GiB; SDK 1.44.3, SigV4/path addressing |
| Metadata/data disks | 18 | Separate named volumes; shared physical Docker filesystem |
| Replication | 3 | Consistent mode, read/write quorum 2; one copy per logical zone |
| Data durability | — | `data_fsync=true`, uncompressed native blocks |
| Metadata | — | SQLite WAL, `metadata_fsync=true`; native hourly snapshots |
| Native RPC timeout | — | 5 s for this local LAN; upstream default is 300 s |

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

## Recovery experiments

Run after `make test`, optionally after `make benchmark`:

| Command | Injection and evidence |
|---|---|
| `make failover` | SIGKILL one native process for about 30 s; persistent disks retained |
| `make disk-failure` | Kill `garage0`, remove its owned data volume, attach an empty replacement; metadata and node identity retained |
| `make repair` | Launch native block repair; this command alone is not proof of completion |
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

## Benchmark

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
  scripts/{config,bootstrap,runtime_config,adapter}.py
  config/generated/                  # ignored native TOML and bootstrap marker
  results/                           # ignored raw evidence
object-storage/
  client/                            # Dockerfile, SDK client, negative controls/audits
  benchmark.py
  fault.py
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
