# Tigris — resilient S3 applications (planned)

Compare S3 application behavior with [Garage](../garage/s3-cluster/),
[SeaweedFS](../seaweedfs/s3-cluster/), [RustFS](../rustfs/s3-cluster/) and
[Ceph RGW](../ceph/s3-cluster/). See the existing
[object-storage results and their limits](../object-storage/).
**No implementation or benchmark has run yet.**

**Current scope (2026-11-22): local isolated Docker/Compose only.** Do not use
host AWS configuration, credentials or real cloud resources. Managed Tigris runs
are deferred. Use the separately named [SeaweedFS lab](../seaweedfs/s3-cluster/)
as the local S3 reference for [Iceberg](../iceberg/multi-system/), with explicit
container endpoints, lab-only credentials and isolated networks. Those results
will describe SeaweedFS, not Tigris.

Tigris offers a managed, globally distributed S3-compatible service. Its public
[`storage` repository](https://github.com/tigrisdata/storage) contains SDKs, not
proof of an available self-hosted storage server. Verify supported deployment
options before adding Compose/kind infrastructure. A service account and an
isolated test bucket are prerequisites for the managed example.
[Official features and consistency modes](https://www.tigrisdata.com/features/).

## Proposed experiments

- Use a container-built Go, Rust or C++ S3 application with independent live
  writers/readers, unique keys/versions, full-body SHA-256 validation and an
  external fsynced acknowledgement witness.
- Exercise PUT/GET/LIST/DELETE, single versus multipart upload, range reads,
  conditional-write conflicts and read-after-write/overwrite visibility. Test
  only documented APIs; record unsupported differences per backend.
- Interrupt the application or its network during multipart upload/completion;
  reconcile ambiguous outcomes, resume safely, then verify complete bodies and
  clean up only example-owned unfinished uploads. Test credential rejection and
  recovery. These are client/network failures, not provider disk failures.
- Investigate bucket snapshots/forks, soft-delete recovery and migration where
  supported. Measure recovered state/RPO against an independent oracle; do not
  treat a snapshot or successful GET as proof of physical replica repair.
- Compare object sizes, concurrency, throughput, successful/failed p50/p99,
  visibility lag, CPU/memory of clients and total cost. Use at least three fresh
  trials, alternate order, and retain raw results. Report first-read versus
  repeated-read behavior without claiming control of provider caches.

## Comparison boundaries

Keep bodies, seed, request mix, client machine/resources, concurrency and timed
boundaries identical. State client/provider geography, network RTT, TLS,
compression, retries, consistency and advertised durability. Provider-side CPU,
RAM and physical placement may be unavailable; a WAN managed service versus
one-host local clusters is an application-level comparison, not an equal-hardware
engine ranking. Optional managed peers: Amazon S3 and Cloudflare R2, after checking
their current API semantics.

The self-hosted examples can inject actual process and owned disk loss and inspect
native repair. Do not claim those tests ran inside Tigris without provider-supported
access and evidence. This lab cannot establish a cloud durability SLA.

[Official pricing](https://www.tigrisdata.com/pricing/) must be checked before a
run; credentials and generated state stay ignored. Implement the five-command
lifecycle with cleanup limited to the example's own bucket, keys and containers.

## Deployment and operations survey

Official documentation reviewed on 2026-11-22; the following are provider claims
and planned checks, not results from this repository.

- **Consistency depends on bucket placement.** Global and dual-region buckets
  advertise strong same-region and eventual cross-region consistency; single-
  and multi-region buckets advertise strong consistency across request regions.
  Record the actual bucket type before evaluating concurrent writes. A single
  local generator cannot demonstrate independent-region behavior.
  [Location contracts](https://www.tigrisdata.com/docs/buckets/locations.md).
- **Conditional writes need negative controls.** Verify stale `If-Match` and
  duplicate `If-None-Match: *` requests reject without changing the stored body.
  Do not infer globally safe compare-and-swap from same-region success.
  [Preconditions](https://www.tigrisdata.com/docs/objects/conditionals.md).
- **TTL and snapshots require separate fixtures.** Conversion to a snapshot
  bucket requires no lifecycle/TTL rules. Preserve this compatibility boundary
  instead of assuming archive snapshots and expiration can share one setup.
  [Snapshot configuration](https://www.tigrisdata.com/docs/buckets/snapshots-and-forks.md).
- **Retention is asynchronous.** Expiration rules use the object's last-modified
  time and support prefix filters. Test overwrite/expiry races, nonmatching
  prefixes and observed deletion lag; accepting a rule is not evidence that
  deletion occurred. [Expiration](https://www.tigrisdata.com/docs/buckets/objects-expiration.md).
- **Cold storage needs retrieval testing.** `GLACIER` objects require restore
  before GET; `GLACIER_IR` does not. Verify exact bodies after restore and measure
  the wait separately from request latency. Minimum retention and retrieval
  charges make these unsuitable for an assumed-free short test.
  [Tiers](https://www.tigrisdata.com/docs/objects/tiers.md),
  [pricing](https://www.tigrisdata.com/pricing/).
- **Soft delete is not physical deletion or independent backup.** Deleted
  objects remain billable during retention; a soft-deleted bucket requires
  explicit permanent deletion rather than automatically disappearing with the
  object-retention window. [Soft delete](https://www.tigrisdata.com/docs/buckets/soft-delete.md).
- **S3 compatibility needs body-level checks.** The provider currently documents
  acceptance of a wrong SHA-256 upload checksum and other API limitations. Keep
  an independent content hash, explicitly test rejection, and preserve any
  reproduced failure. This is an upstream observation, not a local reproduction.
  [Compatibility limits](https://www.tigrisdata.com/docs/api/s3.md).

Inspected the public [compatibility tests' object source](https://github.com/tigrisdata-community/s3-api-compat-tests/blob/main/tests/object.go):
conditional-write cases check both rejection and unchanged content; the bad-
checksum case also checks that a rejected upload left no object. This informs
negative controls, but is not access to Tigris's server implementation. Native
compaction, disk replacement, replica repair and provider CPU/memory are outside
this local lab's control and must remain unqualified.
