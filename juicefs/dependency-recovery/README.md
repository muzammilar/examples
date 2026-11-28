# JuiceFS metadata and object-store recovery

A C++ file-publication workload on independent JuiceFS Community 1.4.2 mounts,
durable PostgreSQL 19.0 metadata and local SeaweedFS 4.55 RF3 storage. Separate
metadata and S3 outages demonstrate stalled operations, explicit errors and recovery.
Neither the single metadata server nor the single S3 gateway is highly available.

## Quick start

1. `make docker-up` — build the native workload and start owned dependency services.
2. `make up` — format once and start independent filesystem mounts.
3. `make test` — publish 128 files and verify exact bodies and all three native copies.
4. `make down` — reconcile retained witnesses, unmount and stop owned services.
5. `make docker-down` — remove only this example's infrastructure and volumes.

Requires native ARM64 Docker, Compose, Python 3.10+, `/dev/fuse` inside the Docker
VM, and at least 5 GiB free Docker disk after builds. All 17 configured roles,
including temporary tools, have combined limits of 5 CPUs / 6.9375 GiB; reserve
another 2 GiB. Tested on an 11-CPU, 24.42-GiB ARM64 Docker VM, Linux 6.12.76.
The mounts require SYS_ADMIN and an unconfined AppArmor profile inside that VM.
No host filesystem mount or real AWS account/configuration is used.

`FILES=128 LIVE_FILES=512 PAYLOAD_BYTES=262144` are bounded Make variables.
Each run is capped at 256 MiB and the retained fixture at 512 MiB. Repeated
`make up` verifies existing acknowledged data without reseeding it. A new fixture
requires verified teardown; never delete witnesses to make a failed run pass.

## Layout and isolation

- `file-publication/`: native Dockerfile and mount script. Its meaningful source
  symlink targets tracked C++17/CMake/OpenSSL workload code in this repository.
  `scripts/prepare.py` materializes regular hard links/copies in ignored `.build/`;
  no sibling Makefile, generated state or host application toolchain is required.
- `compose.yaml`, `deploy/`: pinned images, explicit lab-only endpoints/credentials,
  resource limits and the checked-in backend/client contract.
- `scripts/`: standard-library lifecycle, independent byte oracle, resource
  observer, native metadata/object checks and result curator.
- `tests/`: offline oracle and Docker identity regression tests.
- `reports/QUALIFICATION.json`: curated successful and failed outcomes from both
  fixtures; raw logs, histories and logical archives remain ignored in `results/`.
- [Recovery notes](docs/RECOVERY.md): failure mechanisms, diagnosis, runbooks and gaps.

The internal Compose network has no published service ports. Credentials/region
are explicit, AWS configuration files point to `/dev/null`, metadata-service
lookup is disabled and endpoints are checked. No cloud fallback is permitted.
The object backend is SeaweedFS, not Tigris or AWS.

## Contract

Each deterministic 256-KiB SHAKE256 body is exclusively created, written, file
fsynced, closed, renamed and directory fsynced before an external witness is
fsynced. This chain is not one atomic transaction. Failed fsync/close is ambiguous;
never blindly replay it or promote its temporary file. Independent readers verify
complete bodies. A native metadata dump maps inodes/slices to object keys; a
separate oracle checks full bodies on all three SeaweedFS owners.

Metadata fsync, synchronous commit and full-page writes are read back as enabled.
JuiceFS writeback and disk cache are disabled; metadata/open-cache TTLs are zero.
Memory/kernel caches still exist. Historical reads are warm; the S3 fault uses a
fresh third mount before injection. That is client-cold, not physical disk-cold.
Compression is explicitly disabled and measured object bodies match logical sizes:
1.0× compression, with 3× object-body replication. Replication is not compression.

SeaweedFS has three masters, three volume owners, two filers and three etcd roles;
JuiceFS metadata and its S3 gateway are deliberate single failure points. All
copies share one Docker VM, so this does not prove host/power-loss durability.

## Separate recovery commands

| Command | Purpose |
|---|---|
| `make locks` | Cross-mount flock holder/contender contract |
| `make metadata-outage` | Kill PostgreSQL for 15 seconds with live writer/readers |
| `make object-store-outage` | Kill S3 gateway for 20 seconds; warm and fresh-mount reads |
| `make verify` | Reconcile all retained ACKs, ambiguous staging and native RF3 copies |
| `make test-production` | Serial locks, metadata outage, S3 outage and final verification |
| `make backup` | Fenced, hash-verified logical evidence copy; not a restore qualification |
| `make report` | Curate every phase, including rejected and failed probes |

## Executed results — 2026-11-27, level 3

Two distinct fixtures were retained, verified, then fully destroyed. No active lab
remains. These paced publication/recovery trials are diagnostics, not capacity
benchmarks or a filesystem performance ranking.

| Case | Measured outcome |
|---|---|
| Cross-mount lock | Holder/contender passed on separate FUSE superblocks |
| PostgreSQL SIGKILL | 15.189 s outage; ACK count stayed 14 while down, then 512/512 live publications completed. Final 640 ACKs / 167,772,160 logical bytes and 1,920 native copies matched |
| Initial S3 probe | Data reconciled, but target failed a short/full Docker-ID guard defect; retain as a failed harness run |
| Repeated S3 probe | Reused warm mount invalidated cold-read qualification; an in-flight mount refresh also moved the gateway IP. Rejected as an isolated single-fault test |
| Corrected fresh-fixture S3 probe | 20.186 s outage, gateway IP unchanged, fresh-mount read returned EIO. 511 ACKs + one ambiguous fsync; all 639 retained ACKs / 167,510,016 bytes and 1,917 native copies matched |
| Retained setup / teardown | Repeated setup did not reseed; final 16 persistent roles exited 0, no OOM/137, owned containers/networks/volumes removed |

The final ambiguous publication was an empty `.14.tmp`, with no final name and
no ACK. It is not acknowledged data loss. Failed and ambiguous operations remain
separate from successful latency percentiles.

| Per-operation diagnostic | Metadata outage | Corrected S3 outage |
|---|---:|---:|
| Successful publication p50 / p99 | 33.424 / 44.724 ms | 33.494 / 43.729 ms |
| Maximum successful publication | 16,317.980 ms | 48.548 ms |
| Ambiguous publication | none | 21,038.030 ms, EIO |
| Maximum ACK completion gap | 16,421 ms | 21,284 ms |
| Warm historical body read p50 / p99 / max | 8.382 / 12.243 / 15,502.515 ms | 8.377 / 10.663 / 21.400 ms |

The corrected cold probe reported EIO 319 ms after its process header; this is
not an isolated RPC latency. Successful p99 alone hides the S3 outage completely.
The confounded IP-movement run had a 66,515-ms ACK gap and 11 ambiguous attempts;
its native logs kept dialing the old gateway address after restart. Pinned source
uses a one-minute DNS cache. See the bounded diagnosis and uncertainty in the notes.

| Whole-case resource observation | Metadata outage | Corrected S3 outage |
|---|---:|---:|
| Sampled native CPU delta, lower bound | 49.251 core-s | 54.790 core-s |
| Peak sampled charged memory | 2,016,030,720 B | 2,085,179,392 B |
| Disk read / write bytes | 73,728 / 441,647,104 | 0 / 456,425,472 |
| Settled named-volume allocated / apparent | 957,407,232 / 1,005,371,448 B | 939,917,312 / 987,881,296 B |

Zero block reads means cached I/O in this VM, not zero read work. JSON includes
per-role CPU/utilization, memory, network and block-byte samples, image identities,
client settings and native tool counters. Restart epochs are separate. Sampling
misses first/last tails and some short actors; host/build costs and native physical
IOPS/service latency are unavailable. Named-volume size is a settled observation,
not continuous peak disk usage. Tool counters overlap container samples; do not add.

## Limits and next pass

Automatic metadata HA, metadata disk loss, gateway endpoint movement as an isolated
fault, partitions, CSI deployment, long-term trash/GC, independent backup restore,
physical power loss and a cache/capacity comparison remain unrun. `trash-days=1`
is configuration, not a verified expiration result. The logical archive preserves
failed-fixture evidence; it is not a backup/restore claim. Requested client timeouts
and worker deadlines do not establish universal cancellation bounds.

Sources: [1.4.2 release](https://github.com/juicedata/juicefs/releases/tag/v1.4.2),
[metadata engines](https://juicefs.com/docs/community/databases_for_metadata/),
[cache semantics](https://juicefs.com/docs/community/guide/cache/),
[pinned DNS transport](https://github.com/juicedata/juicefs/blob/v1.4.2/pkg/object/restful.go),
[pinned SQL retries](https://github.com/juicedata/juicefs/blob/v1.4.2/pkg/meta/sql.go).
