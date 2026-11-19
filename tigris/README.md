# Tigris — resilient S3 applications (planned)

Compare S3 application behavior with [Garage](../garage/s3-cluster/),
[SeaweedFS](../seaweedfs/s3-cluster/), [RustFS](../rustfs/s3-cluster/) and
[Ceph RGW](../ceph/s3-cluster/). See the existing
[object-storage results and their limits](../object-storage/).
**No implementation or benchmark has run yet.**

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
