# S3 object-storage comparison

Standalone clustered S3 examples with external byte/ACK accounting and native
repair checks. Garage, SeaweedFS, RustFS and ClickHouse have passed their
correctness, benchmark, separate crash/disk-loss and cleanup workflows. Ceph also
passed independent whole-guest crash and permanent BlueStore disk replacement
tests, including persistence across a restart of the repaired guest.

Each store uses its native replication/erasure coding and recovery tools. The
shared client uses the AWS SDK; it does not implement S3 signatures or storage
placement. Every example keeps the five-command Docker lifecycle and its own
owned state, resource caps, failure tests and measured README.

## Qualification and scope

| Store | Selected release | Scope and important boundary |
|---|---|---|
| SeaweedFS | 4.53 | Separate master, replicated volume, filer/HA metadata and S3 roles; explicit fsync storage rules and native repair |
| Garage | 2.5.0 | Consistent mode, three copies across three logical zones; explicit data fsync and metadata durability policy; bucket versioning is unsupported |
| RustFS | 1.0.5 | Stable upstream GA line; fresh four-node/one-drive deployments with EC2+2 only; shorter operational history is disclosed rather than equated with the others |
| Ceph RGW | Tentacle 20.2.5 | Maintained stable branch; BlueStore OSDs, MON quorum, CRUSH failure domains, native replacement/backfill and separate RGW pools |

A production architecture is exercised by a bounded local lab. Docker containers,
virtual disks and VMs still share one physical computer; local results do not
certify independent-host power-loss durability or production capacity. Upstream
GA status and successful local tests are distinct evidence.

RustFS has open 1.0.5 reports involving legacy MinIO-adopted repair queues and
single-disk commit stalls. These are upstream reports, not reproductions by this
repository. They are qualification inputs and documented exclusions, not grounds
to hide failures or to call a local test a production certification.

## Observed results

2026-11-15, Apple M4 Pro, Docker Linux aarch64, 11 CPUs / 24.42 GiB RAM.
Docker Compose 5.1.4; GNU Make 4.4.1.
These are different replication geometries and resource caps, not a normalized
product ranking. GET follows PUT with warm backend/OS caches and full body hashing.
Ceph uses QEMU TCG and must remain outside a native-container throughput ranking.

| Example | Redundancy | 1 MiB, 16 clients PUT / GET MiB/s | Verified fault behavior |
|---|---|---:|---|
| [Garage](../garage/s3-cluster/) | 3 zone copies, quorum 2, data fsync | 166.08 / 479.11 | Process + empty data disk; all ACKs retained; 3-zone physical block audit |
| [SeaweedFS](../seaweedfs/s3-cluster/) | `200`: 3 datacenter copies, fsync; etcd3 metadata | 185.71 / 1,135.76 | Volume process + empty disk; separate master/etcd/filer/S3 crashes; direct full bytes per required copy |
| [RustFS](../rustfs/s3-cluster/) | Four-node EC2+2, strict protected writes | 222.21 / 995.36 | Process + empty disk; canonical deep heal and reconstructed payload fingerprints |
| [Ceph RGW](../ceph/s3-cluster/) | 9 BlueStore OSDs, host-domain size3/min2 | 13.47 / 33.73 (TCG emulation) | Guest crash + SCSI loss/replacement/restart; 257 completed PG deep scrubs; zero lost ACKs |
| [ClickHouse + Garage](../clickhouse/s3-tiered-storage/) | 3 compute replicas, separate S3 prefixes | Query p50: hot 2.85 ms; S3 250.75 ms; warm cache 3.83 ms | 2 million baseline + 930,000 fault rows/replica, no missing ACKs across 3 separate faults |

Each native S3 benchmark executed 41,536 timed requests with zero request failures.
Ceph executed two qualified 4,384-request matrices with zero failures; the table
uses the latest. Ceph disk recovery reconciled 6,006 objects / 607,653,888 bytes;
its guest-crash test reconciled 4,789 objects / 567,775,232 bytes. Native-clean
times including the deliberate 30-second holds were 382.83 s and 187.47 s
respectively. The guest crash delayed the next successful PUT by 30.00 s.
Fault-window request errors/ambiguous outcomes are reported separately in each
README; zero acknowledged data loss does not imply uninterrupted availability.
All physical zones/drives are logical on one machine. Local passes do not certify
production hardware durability, correlated power-loss behavior or disaster recovery.

The [SeaweedFS 2026-11-16 follow-up](../seaweedfs/s3-cluster/) separately qualified
independent read observation, elected-role faults, preserved ACK histories,
rebuilt-member restart and cold reopen: 36,893 final objects with zero lost ACKs
or corrupt bodies. One gateway GET failed with HTTP 503; its successful-read
completion gap reached 10.123 s. Another 41,536-request warm benchmark had zero
failures (4 KiB / one client: 172.58 PUT ops/s, 8.35 ms p99). Its results do not
qualify the other stores' older combined read/write fault loops.

The [Garage 2026-11-16 follow-up](../garage/s3-cluster/) also uses independent
read/write containers, retained histories and the exact native assignment table.
It reconciled 13,817 ACK objects across eight histories and 139,248 required native
block copies without loss/corruption. Native metadata sweeps are explicit recovery
assists; idle workers alone did not prove all-copy repair. Failed and manually
assisted investigations remain labelled. The single router caused 442 GET errors
and a 30.478 s successful-read gap. Its follow-up results are separate from the
2026-11-15 benchmark table above. The final peer/recovery configuration passed
another three-case live aggregate (3,035 ACK objects / 39,990 required native
copies) and a 41,536-request warm benchmark with zero request failures:
1 MiB / 16 clients measured 155.14 PUT MiB/s / 458.55 GET MiB/s. These remain
separate, non-normalized observations. Final full readback verified 33,936 objects
and 106,902 required native copies; retained whole-stack reopen passed in 57.673 s.

The [RustFS 2026-11-17 follow-up](../rustfs/s3-cluster/) qualified independent
live writer/reader roles, retained ACK histories and native EC2+2 recovery.
The larger fixture retained **15,910 ACK objects across eight histories**, including
two failed cases recovered without application replay; both remain labelled failed.
Post-benchmark readback checked **46,811 objects / 2,424,705,024 bytes**, 187,244
metadata paths and 3,584 immutable payload fingerprints without reconciliation
errors. A separate final-source fresh aggregate passed four live faults with
**6,227 ACKs**, zero lost/corrupt bodies, and a corrected 86.971 s whole-stack
reopen comparing pre-stop fingerprints. Both fresh lifecycles and all three
cleanups passed. Four metadata paths do not independently prove four inline
payloads; native protected deep scan and full external GETs retain separate roles.
Its new 41,536-request warm benchmark had zero failures: 1 MiB / 16 clients
measured **177.52 PUT MiB/s / 673.80 GET MiB/s**. These are separate observations
from the older table, with the example's stated budgets and no normalized ranking.

The [Ceph 2026-11-17 follow-up](../ceph/s3-cluster/) passed separate guest,
empty-disk replacement, rebuilt-member and proxy faults with independent live
writer/reader roles: **10,319 ACKs across four histories**, 11,391 complete objects
and all 321 PGs freshly deep-scrubbed on three distinct guest hosts, without data
loss/corruption. The proxy outage recorded 501 GET errors and a 30.349 s successful
read gap. Its separate 4,384-request warm benchmark had zero failures: 1 MiB /
16 clients measured **19.11 PUT MiB/s / 40.91 GET MiB/s**. These QEMU TCG results
remain outside any native-container ranking; resource caps and sample sizes are
listed in the example. Full-stack cold reopen passed in 347.914 s with all three
guest boot IDs changed and native cluster/OSD UUIDs unchanged; full bodies and
fresh all-PG scrubs passed. A fresh default five-command lifecycle also passed:
1,069 complete objects / 106,860,544 bytes, preserved archived witness hashes,
and zero owned resources/build tags after both cleanups. All ten unrelated
volume identities stayed unchanged.

## Common test contract

- Byte-for-byte SHA-256 verification of complete object bodies; ETag is not used
  as a universal checksum, particularly for multipart uploads.
- PUT/GET/HEAD, pagination and prefixes, ranged reads, overwrite/delete,
  multipart completion/abort, and the declared supported S3 feature subset.
- Separate process-crash, destructive owned-disk-loss/replacement and
  metadata/quorum experiments. Continuous requests and an external fsynced
  attempt/ACK journal; unresolved writes are never blindly replayed.
- Native placement/replica checks and repair completion, followed by full object
  reconciliation. Count missing ACKs, corrupt bodies, stale versions and errors.
- Eleven shared negative controls, including optional ambiguous benchmark outcomes.
  Prepared benchmark bodies are recorded before submission outside the timing
  window; acknowledged outcomes are required separately. Failed runs remain
  failed and their acknowledged data is reconciled before cleanup.
- Correctness before measured PUT/GET runs, bounded dataset/disk consumption,
  explicit durability policy, concurrency, resource caps and warm/cold conditions.

## ClickHouse and ZFS

ClickHouse's new example uses its native S3 disk, filesystem cache and hot/cold
storage policy. It complements the existing Iceberg examples instead of
presenting MergeTree parts as Iceberg tables. Query keys follow the workload;
partitions follow retention boundaries (`schema-partition-lifecycle`), with
substantial insert batches (`insert-batch-size`).

ZFS is a separate filesystem/redundancy/snapshot baseline. It is not an S3
endpoint or a distributed object-store coordinator. Existing ZFS fio results use
software-emulated guests and cannot be ranked against native-container S3
throughput. Useful comparisons cover API semantics, durability boundaries,
checksums/scrubbing, disk replacement, replication RPO and recovery cost.

## Upstream sources

- [SeaweedFS production topology](https://github.com/seaweedfs/seaweedfs/blob/4.53/note/slides/seaweedfs-production-setup.md), [4.53 release](https://github.com/seaweedfs/seaweedfs/releases/tag/4.53).
- [Garage production guide](https://garagehq.deuxfleurs.fr/documentation/cookbook/real-world/), [S3 compatibility](https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/).
- [RustFS 1.0.5](https://github.com/rustfs/rustfs/releases/tag/1.0.5), [reported legacy repair issue](https://github.com/rustfs/rustfs/issues/9370), [reported single-disk stalls](https://github.com/rustfs/rustfs/issues/9365).
- [Ceph maintained releases](https://docs.ceph.com/en/latest/releases/), [S3 API](https://docs.ceph.com/en/tentacle/radosgw/s3/).

## Planned local S3 comparisons

[Apache Ozone and the existing S3 clusters](../docs/example-plans/storage-and-lakehouse.md#object-and-filesystem-storage) have proposed multipart recovery tests. Use the same object bodies and compare metadata availability, successful readback and restored storage redundancy independently.

The [self-hosted Tigris comparison](../tigris/) is blocked until a supported local server distribution and its deployment terms are available. The four measured stores above provide reference results for their own configurations.
